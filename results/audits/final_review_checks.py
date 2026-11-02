import ast, contextlib, io, math, multiprocessing as mp, os, queue, subprocess, sys, tempfile, threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

def put_large(q):
    q.put((0,[1.0]*100000,False))

def main():
    from benchmark_runner.workers import run_mongodb_worker
    from benchmark_runner.metrics import PrometheusCollector
    from benchmark_runner.status import classify_status
    from benchmark_runner.preflight import check_prometheus
    os.chdir(ROOT)
    tree=ast.parse(Path('benchmark_runner/__main__.py').read_text())
    expr=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='plan_out' for t in n.targets))
    paths=[]
    for e in ['mongodb','postgresql']:
      for p in ['PROFILE_A_BASELINE','PROFILE_B_MEDIUM','PROFILE_C_SEVERE']:
       for q, variants in [('Q1',['default']),('Q2',['allowDiskUse_false','allowDiskUse_true'] if e=='mongodb' else ['work_mem_4MB','work_mem_64MB'])]:
        for v in variants:
         for c in [1,10,50]:
          for r in [1,2,3]:
           a=SimpleNamespace(engine=e,profile=p,query=q,variant=v,concurrency=c,run_number=r)
           paths.append(eval(compile(ast.Expression(expr),'<plan_out>','eval'),{'artifact_namespace':'metrics','args':a}))
    print(f'PLAN COLLISION: {len(paths)} runs generate only {len(set(paths))} distinct plan paths')
    source=Path('run_benchmarks.sh').read_text()
    func=source[source.index('is_run_completed() {'):source.index('\n# ---',source.index('is_run_completed() {'))]
    with tempfile.TemporaryDirectory() as d:
      csv=Path(d)/'metrics.csv'; csv.write_text('1,mongodb,PROFILE_A_BASELINE,Q1,1,1,,,,,,,,error\n')
      code=subprocess.run(['bash','-c','CSV_OUTPUT="$1"\n'+func+'\nis_run_completed mongodb PROFILE_A_BASELINE Q1 1 1','audit',str(csv)]).returncode
      print(f'RESUME: error row accepted as completed={code==0}')
    clock=[0.0]
    def aggregate(*a,**kw):
      clock[0]+=2.0
      return SimpleNamespace(to_list=lambda **kw: [])
    coll=SimpleNamespace(aggregate=aggregate)
    client=Mock(); client.__getitem__=Mock(return_value={'orders':coll})
    out=queue.Queue(); ready=queue.Queue(); start=threading.Event(); start.set(); err=threading.Event()
    with patch('benchmark_runner.workers.pymongo.MongoClient',return_value=client),patch('benchmark_runner.workers.time.monotonic',side_effect=lambda:clock[0]):
      run_mongodb_worker(0,[],False,0,1,out,err,ready,start)
    result=out.get_nowait()
    print(f'MEASUREMENT TAIL: duration=1s, simulated query=2s, latencies counted={result[1]}, worker_error={result[2]}')
    resp=Mock(); resp.json.return_value={'status':'success','data':{'result':[{'value':[0,'NaN']}]}}
    with patch('benchmark_runner.metrics.requests.get',return_value=resp):
      nan=PrometheusCollector().query_instant('x')
    print(f'NONFINITE: instant NaN accepted={math.isnan(nan)}, resulting status={classify_status(False,False,False,nan is not None)}')
    resp.json.return_value={'status':'success','data':{'result':[{'values':[[100,'10']]}]}}
    with patch('benchmark_runner.metrics.requests.get',return_value=resp):
      sparse=PrometheusCollector().query_range_max('x',100,400)
    print(f'COVERAGE: one sample in 300s window accepted={sparse}')
    targets=Mock(); targets.json.return_value={'data':{'activeTargets':[{'labels':{'job':j},'health':'up'} for j in ['cadvisor','node-exporter']]}}
    nonfinite=Mock(); nonfinite.json.return_value={'status':'success','data':{'result':[{'value':[0,'NaN']}]}}
    def fake_get(url,**kw):
      return targets if url.endswith('/targets') else nonfinite
    with patch('benchmark_runner.preflight.requests.get',side_effect=fake_get),patch('benchmark_runner.preflight.subprocess.run',return_value=SimpleNamespace(stdout='cid')):
      check_prometheus('http://test','db')
    print('PREFLIGHT: nonempty NaN telemetry accepted')
    ctx=mp.get_context('spawn'); q=ctx.Queue(); p=ctx.Process(target=put_large,args=(q,)); p.start(); p.join(1)
    alive=p.is_alive(); data=q.get(timeout=5); p.join(5)
    print(f'QUEUE: child alive after join-before-read={alive}; after draining={p.is_alive()}; exit={p.exitcode}; n={len(data[1])}')
    q.close(); q.join_thread()
if __name__=='__main__': main()
