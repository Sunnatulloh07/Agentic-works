"""Additive migration and end-to-end service-level integration regressions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_runtime.engine import Engine, Conflict
from platform_runtime.tools import build_registry
from platform_runtime.usage_budget import UsageBudget
from platform_runtime.agent_planner import ResultPlanner
from platform_runtime.knowledge import KnowledgeStore


class UpgradeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'platform.db'
        self.policy=lambda t,a:{'tools':['reports.summary','knowledge.search'],'ladder':'autonomous'}
        self.e=Engine(self.path,build_registry(),self.policy)
    def test_idempotent_initialization_preserves_existing_task(self):
        task=self.e.submit('a','web','old','ops',[{'tool':'reports.summary','args':{}}],'owner')
        self.e.tick('a')
        for _ in range(3):Engine(self.path,build_registry(),self.policy)
        self.assertEqual('succeeded',self.e.get('a',task)['status'])
        with self.e.read() as db:self.assertEqual([1,2,3,4,5,6,7,8],[r[0] for r in db.execute('SELECT version FROM p_migrations ORDER BY version')])
    def test_result_planner_local_mode_without_secret_with_meter(self):
        budget=UsageBudget(self.e);budget.configure('a','owner','USD',10000)
        cfg={'llm':{'provider_mode':'local_loopback','base_url':'http://127.0.0.1:11434/v1','model':'unit-model',
                    'agent_loop_enabled':True,'usage_budget':{'currency':'USD','input_micro_per_million':1,'output_micro_per_million':2}}}
        calls=[]
        def transport(url,body,headers):
            calls.append((url,headers));return {'usage':{'prompt_tokens':10,'completion_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':'{"action":"ask","question":"Qaysi hisobot?"}'}}]}
        planner=ResultPlanner(self.e,transport)
        context={'run_id':'unit','call_index':1,'agent':'ops','input':'Hisobot','remaining_steps':2,'remaining_calls':2,'observations':[]}
        with patch('platform_runtime.agent_planner.config',return_value=cfg),patch('platform_runtime.agent_planner.secret') as secret:
            self.assertEqual('ask',planner('a',context)['action']);secret.assert_not_called()
            with self.assertRaises(Conflict):planner('a',context)
        self.assertEqual(1,len(calls));self.assertEqual({},calls[0][1]);self.assertEqual(1,budget.summary('a')['spent_micro'])
    def test_new_knowledge_tables_survive_engine_restart(self):
        kb=KnowledgeStore(self.e);kb.create_collection('a','owner','manual');kb.grant('a','owner','manual','ops')
        kb.ingest('a','owner','manual','one','Title','O‘zbek savdo hisobotlari')
        other=Engine(self.path,build_registry(),self.policy)
        self.assertEqual('one',KnowledgeStore(other).search('a','ops','manual','savdo')['matches'][0]['document'])
