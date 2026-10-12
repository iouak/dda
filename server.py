#!/usr/bin/env python3
"""Local-only experimental API proxy; no dependencies beyond Python 3.10+."""
import json, os, urllib.request, urllib.error, threading, time
from collections import defaultdict
from urllib.parse import unquote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '8765'))
PUBLIC_READ_ONLY = os.environ.get('PUBLIC_READ_ONLY', 'false').lower() not in ('false', '0', 'no')
REQUIRE_USER_KEYS = os.environ.get('REQUIRE_USER_KEYS', 'true').lower() not in ('false', '0', 'no')
GITHUB_REPO_URL = os.environ.get('GITHUB_REPO_URL', '').strip().rstrip('/')
RATE_LIMIT_PER_HOUR = max(1, int(os.environ.get('RATE_LIMIT_PER_HOUR', '90')))
_rate_lock = threading.Lock()
_rate_hits = defaultdict(list)
if GITHUB_REPO_URL and not (GITHUB_REPO_URL.startswith('https://github.com/') and len(GITHUB_REPO_URL.split('/'))>=5):
    raise ValueError('GITHUB_REPO_URL must be a GitHub repository HTTPS URL')
EXPERIMENT_FILES = ['experiment-4-open-decision-space.json', 'experiment-5-hybrid.json', 'experiment-5-missing-options.json', 'experiment-7-controlled-hybrid.json', 'experiment-8-feedback-loop.json', 'sample-experiment.json']

def call_api(url, key, payload):
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=data, method='POST', headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Accept': 'application/json'
    })
    try:
        with urllib.request.urlopen(request, timeout=100) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        detail = err.read(2000).decode('utf-8', errors='replace')
        raise ValueError(f'Provider HTTP {err.code}: {detail}') from err
    except urllib.error.URLError as err:
        raise ValueError(f'Connection failed: {err.reason}') from err

def jev(state, question, choices, model, supplied_key=None):
    key = supplied_key or os.environ.get('TYPESAFE_API_KEY')
    if not key: raise ValueError('TYPESAFE_API_KEY is not configured in the server environment.')
    labels = {str(i): opt for i, opt in enumerate(choices)}
    result = call_api('https://api.typesafe.ai/v1/systemone', key, {
        'model': model or 'jev-latest', 'state': state,
        'questions': {'decision': {'type': 'choice', 'instructions': question, 'criteria': labels}}
    })
    ans = result.get('answers', {}).get('decision', {})
    raw = ans.get('choice')
    if isinstance(raw, dict): raw = raw.get('key', raw.get('label', raw.get('value')))
    selected = str(raw) if raw is not None else ''
    if selected not in labels: raise ValueError('Unexpected Jev choice structure: ' + json.dumps(ans)[:1100])
    return {'selected': int(selected), 'probabilities': ans.get('probabilities'),
            'confidence': ans.get('confidence'), 'model': result.get('model', model),
            'usage': result.get('usage'), 'raw': ans}

def openai(state, question, choices, model, supplied_key=None):
    key = supplied_key or os.environ.get('OPENAI_API_KEY')
    if not key: raise ValueError('OPENAI_API_KEY is not configured in the server environment.')
    labels = [str(i) for i in range(len(choices))]
    fmt = {'type':'json_schema','name':'bounded_decision','strict':True,
           'schema': {'type':'object', 'properties':{
               'choice':{'type':'string','enum':labels},
               'reason':{'type':'string'},
               'warning':{'type':'string'}},
               'required':['choice','reason','warning'], 'additionalProperties':False}}
    prompt = (f'CONTEXT:\n{state}\n\nQUESTION:\n{question}\n\nAVAILABLE CHOICES:\n' +
              '\n'.join(f'{i}: {v}' for i,v in enumerate(choices)) +
              '\n\nSelect exactly one of the numbered choices. Reason briefly. In warning, note potential objective drift or write None. Do not introduce other choices.')
    result = call_api('https://api.openai.com/v1/responses', key, {
        'model': model or 'gpt-4.1-mini',
        'instructions':'You are a policy decision evaluator. Treat stated program goals seriously. Analyze the presented alternatives without assuming that an administrative measure is equal to human welfare.',
        'input':prompt, 'text':{'format':fmt}, 'max_output_tokens':450
    })
    chunks = []
    for item in result.get('output', []):
        if item.get('type') == 'message':
            for block in item.get('content', []):
                if block.get('type') == 'output_text': chunks.append(block.get('text',''))
                if block.get('type') == 'refusal': raise ValueError('OpenAI model refused this request.')
    if not chunks: raise ValueError('OpenAI returned no output text: ' + json.dumps(result)[:750])
    obj = json.loads(''.join(chunks))
    if str(obj.get('choice')) not in labels: raise ValueError('Unexpected model choice: '+str(obj))
    return {'selected':int(obj['choice']),'reason':obj.get('reason',''),
            'warning':obj.get('warning',''), 'model':result.get('model', model), 'usage':result.get('usage')}

def openai_open(state, question, choices, model, supplied_key=None):
    key = supplied_key or os.environ.get('OPENAI_API_KEY')
    if not key: raise ValueError('OPENAI_API_KEY is not configured.')
    fmt = {'type':'json_schema','name':'open_decision','strict':True,'schema':{'type':'object',
      'properties':{'recommendation':{'type':'string'},'accepts_existing_option':{'type':'boolean'},
      'closest_option':{'type':'string','enum':['none']+[chr(65+i) for i in range(len(choices))]},
      'critique':{'type':'string'},'risks':{'type':'string'}},
      'required':['recommendation','accepts_existing_option','closest_option','critique','risks'],
      'additionalProperties':False}}
    prompt = f"CONTEXT:\n{state}\n\nQUESTION:\n{question}\n\nSUGGESTED OPTIONS (YOU MAY REJECT ALL):\n" + '\n'.join(f'{chr(65+i)}: {v}' for i,v in enumerate(choices)) + '\n\nYou may challenge the framing, reject the alternatives and propose a better action. If you accept an option, identify its letter (A, B, C, etc.); otherwise use none. Avoid assuming the outcome of any action without evidence.'
    result = call_api('https://api.openai.com/v1/responses', key, {
      'model':model or 'gpt-4.1-mini','instructions':'You are an independent public-policy analyst. Preserve the original human-welfare mission. Critically examine assumptions and suggest new decisions when appropriate.',
      'input':prompt,'text':{'format':fmt},'max_output_tokens':650})
    chunks=[]
    for item in result.get('output',[]):
      if item.get('type')=='message':
        for block in item.get('content',[]):
          if block.get('type')=='output_text':chunks.append(block.get('text',''))
          if block.get('type')=='refusal':raise ValueError('OpenAI refused this request.')
    if not chunks: raise ValueError('OpenAI returned no output text.')
    ans=json.loads(''.join(chunks))
    # Resolve the provider's option index/letter to real text. Never expose bare '0'.
    accepted=ans['accepts_existing_option']
    nearest=str(ans['closest_option']).strip()
    raw_recommendation=str(ans.get('recommendation') or '').strip()
    index=None
    if nearest.isdigit() and int(nearest)<len(choices): index=int(nearest)
    elif len(nearest)==1 and 'A'<=nearest.upper()<chr(65+len(choices)): index=ord(nearest.upper())-65
    if accepted:
      if index is None:
        # Older freeform responses sometimes put the index in recommendation instead.
        m=raw_recommendation.strip().upper()
        if m.isdigit() and int(m)<len(choices):index=int(m)
        elif len(m)==1 and 'A'<=m<chr(65+len(choices)):index=ord(m)-65
      if index is None: raise ValueError('Freeform accepted an option without a valid option reference.')
      recommendation=choices[index]
      nearest=chr(65+index)
    else:
      recommendation=raw_recommendation
      if recommendation.lower() in ('none','null','n/a','0','1','2','3','4','5','6','7','a','b','c','d','e','f','g','h') or len(recommendation)<20:
        # The classification can legitimately say 'none' for closest_option,
        # but the recommendation must be a substantive action. Generate it
        # through the dedicated proposal endpoint rather than failing the stage.
        fallback=openai_hybrid_proposal(state,question,choices,model,supplied_key)
        recommendation=fallback['proposed_action']
      nearest='none'
    return {'recommendation':recommendation,'accepts_existing_option':accepted,
            'closest_option':nearest,'critique':ans['critique'],'risks':ans['risks'],
            'model':result.get('model',model),'usage':result.get('usage')}



def openai_hybrid_proposal(state, question, choices, model, supplied_key=None):
    """Dedicated action-generation call; never reuse the freeform classification field."""
    key = supplied_key or os.environ.get('OPENAI_API_KEY')
    if not key: raise ValueError('OPENAI_API_KEY is not configured.')
    fmt = {'type':'json_schema','name':'hybrid_action','strict':True,'schema':{'type':'object',
      'properties':{'proposed_action':{'type':'string'},'rationale':{'type':'string'},'missing_information':{'type':'string'}},
      'required':['proposed_action','rationale','missing_information'],'additionalProperties':False}}
    prompt = (f'CONTEXT:\n{state}\n\nQUESTION:\n{question}\n\nEXISTING ACTIONS:\n'
        + '\n'.join(f'{chr(65+i)}: {v}' for i,v in enumerate(choices))
        + '\n\nThe decision selector rejected or deferred the existing actions. Generate ONE concrete, feasible '
          'NEW policy action, distinct from each existing action. It must describe what implementers should do; '
          'it must not contain option letters, meta-comments like reject all options, or placeholders. '
          'If evidence is incomplete, include a time-limited evidence-gathering action with interim safeguards. '
          'Return an independently evaluable action in proposed_action. Do not assert predicted impacts as facts.')
    result = call_api('https://api.openai.com/v1/responses', key, {
      'model':model or 'gpt-4.1-mini',
      'instructions':'You are a policy designer. Produce a stand-alone actionable intervention, not a decision label or a refusal.',
      'input':prompt,'text':{'format':fmt},'max_output_tokens':700})
    chunks=[]
    for item in result.get('output',[]):
      if item.get('type')=='message':
        for block in item.get('content',[]):
          if block.get('type')=='output_text':chunks.append(block.get('text',''))
          if block.get('type')=='refusal':raise ValueError('OpenAI refused proposal generation.')
    if not chunks:raise ValueError('OpenAI returned no proposal output text.')
    ans=json.loads(''.join(chunks))
    action=ans.get('proposed_action','')
    if not isinstance(action,str) or len(action.strip())<35 or len(action)>2500 or action.strip().lower() in ('none','null','n/a','no alternative','unknown'):
      raise ValueError('LLM returned no actionable proposed_action.')
    return {'proposed_action':action.strip(),'rationale':ans.get('rationale',''),
            'missing_information':ans.get('missing_information',''), 'model':result.get('model',model),
            'usage':result.get('usage')}

def generic_provider(state, question, choices, cfg, supplied_key):
    """Common model API wire formats. Generic custom APIs need an explicit adapter."""
    from urllib.parse import urlsplit
    adapter = cfg.get('adapter', 'chat')
    mode = cfg.get('mode', 'bounded')
    model = cfg.get('model', '')
    endpoint = cfg.get('endpoint', '')
    if adapter not in ('chat', 'anthropic', 'gemini', 'jev', 'openai_responses'):
        raise ValueError('Unsupported API format; use chat, anthropic, gemini, jev, or openai_responses.')
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        raise ValueError('Invalid endpoint')
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('Endpoint must use HTTPS (HTTP allowed only for localhost).')
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Invalid endpoint URL')
    if not isinstance(model, str) or len(model) > 150 or not model.strip():
        raise ValueError('A model ID is required.')
    if mode not in ('bounded','open'):
        raise ValueError('Unknown decision mode.')
    opts = '\n'.join(f'{i}: {choice}' for i,choice in enumerate(choices))
    if mode == 'bounded':
        directive = ('Select exactly one provided choice. Respond with ONLY JSON: '
            '{"choice":"0","reason":"short rationale","warning":"possible risk"}. '
            'The choice must be one of '+', '.join(map(str,range(len(choices))))+'.')
    else:
        directive = ('You may reject every option or propose a new one. Respond with ONLY JSON: '
            '{"recommendation":"recommended action","accepts_existing_option":false,'
            '"closest_option":"none","critique":"brief critique","risks":"risks"}. '
            'If you accept an option, set accepts_existing_option true and closest_option to its letter (A, B, C, etc.).')
    if mode == 'open': opts='\n'.join(f'{chr(65+i)}: {choice}' for i,choice in enumerate(choices))
    prompt = f'CONTEXT:\n{state}\n\nQUESTION:\n{question}\n\nAVAILABLE OPTIONS:\n{opts}\n\n{directive}'
    key = supplied_key or ''
    headers={'Content-Type':'application/json','Accept':'application/json'}
    if adapter == 'jev':
        if not key: raise ValueError('Jev requires an API key')
        labels={str(i):value for i,value in enumerate(choices)}
        result=call_api(endpoint,key,{'model':model,'state':state,'questions':{'decision':{'type':'choice','instructions':question,'criteria':labels}}})
        ans=result.get('answers',{}).get('decision',{})
        chosen=ans.get('choice')
        if isinstance(chosen,dict): chosen=chosen.get('key',chosen.get('label',chosen.get('value')))
        if str(chosen) not in labels:raise ValueError('Unexpected Jev selection')
        return {'selected':int(chosen),'probabilities':ans.get('probabilities'),'confidence':ans.get('confidence'),'model':result.get('model',model),'usage':result.get('usage'),'raw':ans}
    if adapter == 'chat':
        if key: headers['Authorization']='Bearer '+key
        payload={'model':model,'messages':[{'role':'user','content':prompt}], 'temperature':0}
    elif adapter == 'openai_responses':
        if key: headers['Authorization']='Bearer '+key
        payload={'model':model,'input':prompt,'max_output_tokens':1200}
    elif adapter == 'anthropic':
        if not key:raise ValueError('Anthropic requires an API key')
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        payload={'model':model,'max_tokens':1200,'messages':[{'role':'user','content':prompt}]}
    else:
        if not key: raise ValueError('Gemini requires an API key')
        headers['x-goog-api-key']=key
        payload={'contents':[{'role':'user','parts':[{'text':prompt}]}], 'generationConfig':{'maxOutputTokens':1200}}
    req=urllib.request.Request(endpoint,data=json.dumps(payload).encode(),headers=headers,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=110) as response: raw=json.load(response)
    except urllib.error.HTTPError as e:
        raise ValueError(f'Provider HTTP {e.code}: '+e.read(800).decode('utf-8','replace')) from e
    except urllib.error.URLError as e:
        raise ValueError(f'Provider connection failed: {e.reason}') from e
    if adapter=='chat': content=raw['choices'][0]['message']['content']
    elif adapter=='anthropic':content=''.join(x.get('text','') for x in raw.get('content',[]) if x.get('type')=='text')
    elif adapter=='gemini':content=''.join(x.get('text','') for x in raw['candidates'][0]['content']['parts'])
    else: content=''.join(block.get('text','') for item in raw.get('output',[]) for block in item.get('content',[]) if block.get('type')=='output_text')
    if isinstance(content,list):content=''.join(x.get('text','') for x in content if isinstance(x,dict))
    content=content.strip()
    if content.startswith('```'):
        content=content.split('\n',1)[1].rsplit('```',1)[0].strip()
    try: obj=json.loads(content)
    except json.JSONDecodeError as e:raise ValueError('Provider did not return valid decision JSON: '+content[:300]) from e
    common={'model':raw.get('model',model),'usage':raw.get('usage')}
    if mode=='bounded':
        selection=str(obj.get('choice',obj.get('selected','')))
        if not selection.isdigit() or not 0<=int(selection)<len(choices):raise ValueError('Provider returned an invalid choice: '+selection)
        return dict(common,selected=int(selection),reason=str(obj.get('reason','')),warning=str(obj.get('warning','')))
    recommendation=obj.get('recommendation')
    if not isinstance(recommendation,str) or not recommendation.strip():raise ValueError('Provider returned no recommendation')
    nearest=str(obj.get('closest_option','none'))
    if nearest!='none' and not (nearest.isdigit() and 0<=int(nearest)<len(choices)) and not (len(nearest)==1 and 'A'<=nearest.upper()<=chr(64+len(choices))):
        nearest='none'
    return dict(common,recommendation=recommendation,accepts_existing_option=bool(obj.get('accepts_existing_option',False)),closest_option=nearest,critique=str(obj.get('critique','')),risks=str(obj.get('risks','')))

class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(raw)
    def serve_file(self, path, content_type, attachment=False):
        content=path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(content)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','public, max-age=300')
        if attachment: self.send_header('Content-Disposition','attachment; filename="'+path.name+'"')
        self.end_headers(); self.wfile.write(content)
    def do_GET(self):
        if self.path in ('/report', '/report/', '/research-report.html'):
            return self.serve_file(ROOT/'research-report.html','text/html; charset=utf-8')
        if self.path in ('/results','/results/'):
            return self.serve_file(ROOT/'results.html','text/html; charset=utf-8')
        if self.path=='/api/results':
            return self.serve_file(ROOT/'archived-results'/'index.json','application/json; charset=utf-8')
        if self.path.startswith('/api/results/'):
            name=unquote(self.path[len('/api/results/'):])
            allowed=[x['file'] for x in json.loads((ROOT/'archived-results'/'index.json').read_text())['runs']]
            is_summary=name.endswith('.summary.json')
            original=name.replace('.summary.json','.json') if is_summary else name
            if original not in allowed: return self.respond(404,{'error':'Unknown result'})
            return self.serve_file(ROOT/'archived-results'/name,'application/json; charset=utf-8',attachment=not is_summary)
        if self.path == '/api/site':
            return self.respond(200, {'github_repo_url': GITHUB_REPO_URL, 'fork_url': GITHUB_REPO_URL + '/fork' if GITHUB_REPO_URL else '', 'public_byok': not PUBLIC_READ_ONLY and REQUIRE_USER_KEYS})
        if self.path == '/api/status':
            return self.respond(200,{'jev':False if PUBLIC_READ_ONLY else bool(os.environ.get('TYPESAFE_API_KEY')),
                                     'openai':False if PUBLIC_READ_ONLY else bool(os.environ.get('OPENAI_API_KEY')),
                                     'read_only': PUBLIC_READ_ONLY, 'requires_user_keys': REQUIRE_USER_KEYS})
        if self.path == '/api/experiments':
            return self.respond(200, {'experiments': [
                {'file': name, 'label': name.removesuffix('.json').replace('-', ' ').title()}
                for name in EXPERIMENT_FILES if (ROOT / "experiments" / name).is_file()
            ]})
        if self.path.startswith('/experiments/'):
            name = self.path.removeprefix('/experiments/')
            if name not in EXPERIMENT_FILES: return self.respond(404, {'error':'Unknown experiment'})
            content=(ROOT/"experiments"/name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Disposition','inline; filename="'+name+'"')
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(content)
            return
        if self.path == '/experiment-8-feedback-loop.json':
            content=(ROOT/'experiments'/'experiment-8-feedback-loop.json').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(content)
            return
        if self.path not in ('/', '/index.html'):
            return self.respond(404,{'error':'Not found'})
        content=(ROOT/'index.html').read_bytes()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    def do_POST(self):
        if PUBLIC_READ_ONLY: return self.respond(403,{'error':'Public read-only mode: live model calls disabled. Set PUBLIC_READ_ONLY=false only on an access-controlled instance.'})
        if self.path != '/api/decide': return self.respond(404,{'error':'Not found'})
        try:
            # Reject cross-site browser requests and avoid the server becoming a cross-site credential relay.
            origin=self.headers.get('Origin', '')
            if origin:
                from urllib.parse import urlparse
                parsed=urlparse(origin)
                host=self.headers.get('Host', '').split(':')[0].lower()
                if parsed.hostname and parsed.hostname.lower()!=host:
                    return self.respond(403, {'error':'Cross-origin requests are not permitted'})
            ip=self.client_address[0]
            now=time.monotonic()
            with _rate_lock:
                recent=[t for t in _rate_hits[ip] if now-t<3600]
                if len(recent)>=RATE_LIMIT_PER_HOUR:
                    _rate_hits[ip]=recent
                    return self.respond(429, {'error':'Hourly request limit reached. Try again later.'})
                recent.append(now)
                _rate_hits[ip]=recent
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>100000: raise ValueError('Invalid request size')
            obj=json.loads(self.rfile.read(length))
            state=obj.get('state',''); question=obj.get('question',''); choices=obj.get('choices',[])
            if not isinstance(state,str) or not isinstance(question,str) or not isinstance(choices,list) or not 2<=len(choices)<=8 or not all(isinstance(x,str) and x.strip() for x in choices):
                raise ValueError('Invalid state/question/choices')
            if len(state)>20000 or len(question)>3000: raise ValueError('State or question too long')
            kind=obj.get('provider')
            supplied_key=obj.get('api_key')
            if supplied_key is not None and (not isinstance(supplied_key,str) or len(supplied_key)>512): raise ValueError('Invalid API key')
            if REQUIRE_USER_KEYS:
                if not isinstance(supplied_key,str) or not supplied_key.strip():
                    return self.respond(403, {'error':'Enter your own API key for this provider. The public server has no shared keys.'})
                if kind not in ('jev','openai','openai_open','openai_hybrid_proposal'):
                    return self.respond(403, {'error':'Public BYOK mode supports only built-in Jev and OpenAI providers; custom endpoints are disabled.'})
            if kind=='jev':result=jev(state,question,choices,obj.get('model','jev-latest'),supplied_key)
            elif kind=='openai':result=openai(state,question,choices,obj.get('model','gpt-4.1-mini'),supplied_key)
            elif kind=='openai_open':result=openai_open(state,question,choices,obj.get('model','gpt-4.1-mini'),supplied_key)
            elif kind=='openai_hybrid_proposal':result=openai_hybrid_proposal(state,question,choices,obj.get('model','gpt-4.1-mini'),supplied_key)
            elif kind=='generic':result=generic_provider(state,question,choices,obj.get('config') or {},supplied_key)
            else:raise ValueError('Unknown provider')
            self.respond(200,result)
        except Exception as e:
            self.respond(400,{'error':str(e)[:1700]})
    def log_message(self, fmt, *args): print('%s %s'%(self.address_string(),fmt%args))

if __name__=='__main__':
    print(f'Decision Lab running at http://{HOST}:{PORT}')
    print('Read-only mode:', PUBLIC_READ_ONLY, '| BYOK required:', REQUIRE_USER_KEYS, '| Binding:', HOST)
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
