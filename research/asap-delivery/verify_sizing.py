"""Isolated execution of production functions; not a PostgreSQL lifecycle test."""
import ast, asyncio, importlib.util, sys, types, unittest
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[2]/'backend/sportsassets'
pkg=types.ModuleType('sportsassets');pkg.__path__=[str(ROOT)];sys.modules['sportsassets']=pkg
spec=importlib.util.spec_from_file_location('sportsassets.bettor_paper_limits',ROOT/'bettor_paper_limits.py');P=importlib.util.module_from_spec(spec);sys.modules[spec.name]=P;spec.loader.exec_module(P)
def function(file,name,package='sportsassets',**env):
 tree=ast.parse((ROOT/file).read_text()); node=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
 ns={'__package__':package,'Decimal':Decimal,'D':Decimal,'f':float,**env};exec(compile(ast.Module(body=[node],type_ignores=[]),file,'exec'),ns);return ns[name]
class NoReads:
 async def fetch(self,*a):raise AssertionError('allocation query should be bypassed')
 async def fetchval(self,*a):raise AssertionError('allocation query should be bypassed')
class Capital(unittest.IsolatedAsyncioTestCase):
 def test_target_not_ceiling(self):
  old={'risk':{'per_order_cap_usd':5000},'entry':{'target_order_usd':5000,'pinnacle_max_age_s':30}}
  cfg=P.effective_config(old,P.ACCOUNT_ID)
  self.assertEqual(cfg['entry']['target_order_usd'],1000);self.assertIsNone(cfg['risk']['per_order_cap_usd']);self.assertEqual(old['entry']['target_order_usd'],5000);self.assertEqual(cfg['entry']['pinnacle_max_age_s'],30)
 def test_other_accounts_unchanged(self):
  old={'risk':{'per_order_cap_usd':1234}};self.assertEqual(P.effective_config(old,'other'),old);self.assertIsNone(P.describe('funded'))
 async def test_cash_and_large_orders(self):
  names=['R_INSUFFICIENT','R_PER_ORDER','R_HEDGE_RESERVE','R_GROUPS','R_MARKET','R_FIXTURE']
  fn=function('bettor_paper_ledger.py','_check_caps',**{n:n for n in names})
  for role in ['ENTRY','HEDGE']:
   for reserve in [100,1000,4900,500000]:
    with self.subTest(role=role,reserve=reserve):
     self.assertIsNone(await fn(NoReads(),{'account_id':P.ACCOUNT_ID,'role':role},reserve=Decimal(reserve),cs={'cash':Decimal(500000),'available':Decimal(500000)},caps={'per_order_cap_usd':100,'max_concurrent_groups':1}))
  self.assertEqual((await fn(NoReads(),{'account_id':P.ACCOUNT_ID,'role':'ENTRY'},reserve=Decimal(501),cs={'cash':Decimal(500000),'available':Decimal(500)},caps={}))['refusal'],'R_INSUFFICIENT')
 async def test_fixture_ownership_no_cap(self):
  fn=function('bettor_paper_ledger.py','fixture_owner_refusal')
  self.assertIsNone(await fn(NoReads(),{'account_id':P.ACCOUNT_ID},same_strategy_live=True))
 async def test_cross_strategy_no_cap(self):
  fn=function('agents/paper_benchmark.py','cross_strategy_exposure',package='sportsassets.agents')
  self.assertFalse((await fn(NoReads(),account_id=P.ACCOUNT_ID,strategy='x',slug='a',fixture='b'))['held'])
 async def test_exploration_fixture_no_cap(self):
  fn=function('agents/paper_explore.py','fixture_taken',package='sportsassets.agents',LIMITS=P)
  self.assertFalse(await fn(NoReads(),P.ACCOUNT_ID,'f','s'))
 async def test_main_sampling_selects_every_eligible_valuation(self):
  tree=ast.parse((ROOT/'agents/paper_explore.py').read_text());env={'SAMPLING_WINDOW_S':86400,'VERSION':'PINNACLE_EXPLORATION_PAPER_V3','LIMITS':P,'PB':types.SimpleNamespace(EXPERIMENT_ID='x'),'draw':lambda f:.999999,'inclusion_probability':lambda n:.7}
  for n in tree.body:
   if isinstance(n,ast.Assign):
    try:v=ast.literal_eval(n.value)
    except Exception:continue
    for t in n.targets:
     if isinstance(t,ast.Name):env[t.id]=v
  fn=function('agents/paper_explore.py','selection',package='sportsassets.agents',**env)
  class Counts:
   async def fetchval(self,*a):return 100000
  r=await fn(Counts(),cand={'fixture':'f'},at=1800000000,account_id=P.ACCOUNT_ID)
  self.assertTrue(r['selected']);self.assertEqual(r['selection_probability'],1);self.assertIsNone(r['target_per_sport'])
 def test_no_oversize_cancellation(self):
  self.assertNotIn('obsolete_entry',(ROOT/'bettor_paper_simulator.py').read_text())
if __name__=='__main__':unittest.main(verbosity=2)
