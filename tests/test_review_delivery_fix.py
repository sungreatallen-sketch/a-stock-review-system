"""离线回归：不启动项目、不访问网络、不发送飞书。"""
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

class RegressionTests(unittest.TestCase):
    def setUp(self):
        import os
        env_patch=patch.dict(os.environ);env_patch.start();self.addCleanup(env_patch.stop)
        originals=dict(sys.modules)
        def restore_modules():
            names=set(sys.modules) | set(originals)
            for n in names:
                if n=='app' or n.startswith('app.') or n=='scripts' or n.startswith('scripts.') or n=='requests':
                    if n in originals: sys.modules[n]=originals[n]
                    else: sys.modules.pop(n,None)
        self.addCleanup(restore_modules)
        requests=types.ModuleType('requests')
        requests.Session=type('Session',(),{'trust_env':False})
        requests.post=Mock(side_effect=AssertionError('离线测试禁止真实网络调用'))
        sys.modules['requests']=requests
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = {k: Path(self.tmp.name) / k for k in ('data', 'reports')}
        for p in self.p.values(): p.mkdir()
        for n in list(sys.modules):
            if n == 'app' or n.startswith('app.') or n == 'scripts' or n.startswith('scripts.'):
                sys.modules.pop(n)
        for name, folder in [('app','app'), ('scripts','scripts'), ('app.predict','app/predict')]:
            m = types.ModuleType(name); m.__path__ = [str(ROOT / folder)]; sys.modules[name] = m
        cfg = types.ModuleType('app.config'); cfg.paths = lambda: self.p
        cfg.load_config = lambda: {}; sys.modules[cfg.__name__] = cfg
        checks = types.ModuleType('scripts.check_data_sources')
        self.health = Mock(return_value={'status':'OK'})
        for name in ['check_direct_mcp','check_mcp','check_ths','check_tdx_direct']: setattr(checks,name,self.health)
        sys.modules[checks.__name__] = checks
        alerts = types.ModuleType('scripts.send_feishu_alert'); alerts.send_alert = Mock()
        sys.modules[alerts.__name__] = alerts
        self.utils = module('app.utils','app/utils.py')
        self.auto = module('scripts.auto_review','scripts/auto_review.py')

    def report(self, day='2026-09-14', generated=None):
        d={'date':day,'meta':{'generated_at':generated or day+'T16:03:00'},
           'prediction':{'date':day,'status':'M3完整版','targets':[
               {'code':str(i),'name':'样本','参考买入价(收盘)':10} for i in range(3)]}}
        (self.p['reports'] / (day+'.json')).write_text(json.dumps(d),encoding='utf-8')
        return d

    def clock(self, text):
        from contextlib import ExitStack
        now=datetime.fromisoformat(text)
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None): return now if tz is None else now.replace(tzinfo=tz)
        stack=ExitStack()
        stack.enter_context(patch.object(self.auto,'datetime',Frozen))
        for name in ['app.utils','app.review_delivery']:
            if name in sys.modules and hasattr(sys.modules[name],'market_now'):
                stack.enter_context(patch.object(sys.modules[name],'market_now',return_value=now))
        if hasattr(self.auto,'market_now'): stack.enter_context(patch.object(self.auto,'market_now',return_value=now))
        return stack

    def test_mid_autumn_is_closed(self):
        self.assertFalse(self.utils.is_trading_day(date(2026,9,25)))

    def test_september_21_is_open(self):
        self.assertTrue(self.utils.is_trading_day(date(2026,9,21)))

    def test_spring_festival_is_closed(self):
        self.assertFalse(self.utils.is_trading_day(date(2026,2,18)))

    def test_dragon_boat_is_closed(self):
        self.assertFalse(self.utils.is_trading_day(date(2026,6,19)))

    def test_monday_morning_never_sends_friday(self):
        self.report('2026-09-11')
        self.auto._settle_pending=Mock()
        with self.clock('2026-09-14T09:10:00'), patch.object(self.auto.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as proc:
            self.auto.run_auto_review()
        self.health.assert_not_called()
        proc.assert_not_called()
        self.auto._settle_pending.assert_not_called()

    def test_regenerated_report_does_not_reset_daily_delivery(self):
        self.report('2026-09-11', '2026-09-11T20:21:41')
        (self.p['data']/'last_review_sent_2026-09-11.flag').write_text('2026-09-11T16:03:00')
        self.assertTrue(self.auto.report_sent('2026-09-11'))

    def test_wrong_prediction_date_is_not_final(self):
        d=self.report();d['prediction']['date']='2026-09-11'
        (self.p['reports']/'2026-09-14.json').write_text(json.dumps(d))
        self.assertFalse(self.auto.final_report_ready('2026-09-14'))

    def test_next_day_skips_mid_autumn_and_weekend(self):
        self.assertEqual(self.utils.get_next_trading_day(date(2026,9,24)),date(2026,9,28))

    def test_next_day_skips_national_holiday(self):
        self.assertEqual(self.utils.get_next_trading_day(date(2026,9,30)),date(2026,10,8))

    def test_makeup_working_weekend_is_closed(self):
        self.assertFalse(self.utils.is_trading_day(date(2026,10,10)))

    def test_unknown_year_raises_instead_of_guessing(self):
        with self.assertRaises(self.utils.CalendarUnavailable):
            self.utils.is_trading_day(date(2027,1,4))

    def test_full_time_comparison(self):
        for t in ['16:00:00','17:10:00','20:00:00']:
            self.assertEqual(self.utils.get_latest_closed_trading_day(datetime.fromisoformat('2026-09-14T'+t)),date(2026,9,14))

    def test_utc_clock_is_converted_to_shanghai(self):
        from app.review_delivery import automatic_target
        self.assertEqual(automatic_target(datetime.fromisoformat('2026-09-14T08:00:00+00:00')),'2026-09-14')
        self.assertIsNone(automatic_target(datetime.fromisoformat('2026-09-14T07:59:59+00:00')))

    def test_automatic_gate_time_and_holidays(self):
        from app.review_delivery import automatic_target
        for t in ['2026-09-14T15:59:59','2026-09-19T20:00:00','2026-09-25T16:00:00']:
            self.assertIsNone(automatic_target(datetime.fromisoformat(t)))
        for t in ['2026-09-14T16:00:00','2026-09-14T20:30:00']:
            self.assertEqual(automatic_target(datetime.fromisoformat(t)),'2026-09-14')

    def test_manual_receipt_prevents_later_auto(self):
        from app.review_delivery import mark_report_delivered
        report=self.report()
        with self.clock('2026-09-14T20:30:00'):
            mark_report_delivered(self.p['data'],report,'manual-feishu')
            self.auto.run_auto_review()
        self.health.assert_not_called()

    def test_receipt_retains_first_success(self):
        from app.review_delivery import mark_report_delivered
        report=self.report()
        mark_report_delivered(self.p['data'],report,'manual-feishu')
        fp=self.p['data']/'review_delivery_2026-09-14.json'
        original=fp.read_text()
        report['meta']['generated_at']='2026-09-14T22:00:00'
        mark_report_delivered(self.p['data'],report,'manual-feishu')
        self.assertEqual(original,fp.read_text())

    def test_midday_flag_does_not_block_final(self):
        (self.p['data']/'last_review_sent_2026-09-14.flag').write_text('2026-09-14T10:00:00')
        self.assertFalse(self.auto.report_sent('2026-09-14'))

    def test_corrupt_receipt_does_not_silently_resend(self):
        (self.p['data']/'review_delivery_2026-09-14.json').write_text('broken')
        with self.clock('2026-09-14T20:30:00'), self.assertLogs('auto_review',level='ERROR'):
            self.auto.run_auto_review()
        self.health.assert_not_called()

    def test_lock_serializes_threads_and_is_reentrant(self):
        import threading
        from app.review_delivery import delivery_lock
        result=[]
        with delivery_lock(self.p['data']) as a:
            with delivery_lock(self.p['data']) as b:
                self.assertTrue(a and b)
                def other():
                    with delivery_lock(self.p['data'],blocking=False) as got: result.append(got)
                t=threading.Thread(target=other);t.start();t.join(2)
                self.assertFalse(t.is_alive())
        self.assertEqual(result,[False])
        with delivery_lock(self.p['data'],blocking=False) as got: self.assertTrue(got)

    def sender(self):
        report_text=types.ModuleType('app.report_text');report_text.execution_plan_text=lambda x: str(x)
        sys.modules[report_text.__name__]=report_text
        b=types.ModuleType('app.feishu.bot');b.report_link=lambda day:{'ip':'http://example.invalid','public':''}
        b._send_report_images=Mock();sys.modules[b.__name__]=b
        sender=module('scripts.send_review','scripts/send_review.py')
        sender.send_card=Mock(return_value=True)
        return sender

    def test_standalone_sender_also_blocks_morning(self):
        sender=self.sender();self.report('2026-09-11')
        with self.clock('2026-09-14T09:10:00'):
            self.assertTrue(sender.send_report('2026-09-11','test'))
        sender.send_card.assert_not_called()

    def test_explicit_manual_cli_can_view_friday(self):
        sender=self.sender();self.report('2026-09-11')
        with self.clock('2026-09-14T09:10:00'), patch.object(sender.log,'warning'):
            self.assertTrue(sender.send_report('2026-09-11','test',automatic=False))
        sender.send_card.assert_called_once()

    def test_sender_failure_does_not_mark_success(self):
        sender=self.sender();self.report();sender.send_card.return_value=False
        with self.clock('2026-09-14T20:30:00'):
            self.assertFalse(sender.send_report('2026-09-14','test'))
        self.assertFalse(self.auto.report_sent('2026-09-14'))

    def test_auto_evening_then_repeat_sends_once(self):
        sender=self.sender();self.report()
        with self.clock('2026-09-14T20:30:00'),patch.object(sender.log,'warning'):
            self.auto.run_auto_review()
            self.auto.run_auto_review()
        sender.send_card.assert_called_once()
        self.assertTrue(self.auto.report_sent('2026-09-14'))

    def test_failed_generation_does_not_send_old_report(self):
        sender=self.sender();self.report('2026-09-11')
        self.auto._settle_pending=Mock()
        with self.clock('2026-09-14T20:30:00'),patch.object(self.auto.subprocess,'run',return_value=types.SimpleNamespace(returncode=1)),self.assertLogs('auto_review',level='ERROR'):
            self.auto.run_auto_review()
        sender.send_card.assert_not_called()

    def test_incomplete_generation_is_not_sent(self):
        sender=self.sender()
        self.auto._settle_pending=Mock()
        def generate(*a,**kw):
            d=self.report();d['prediction']['targets']=d['prediction']['targets'][:1]
            (self.p['reports']/'2026-09-14.json').write_text(json.dumps(d))
            return types.SimpleNamespace(returncode=0)
        with self.clock('2026-09-14T20:30:00'),patch.object(self.auto.subprocess,'run',side_effect=generate),self.assertLogs('auto_review',level='ERROR'):
            self.auto.run_auto_review()
        sender.send_card.assert_not_called()

    def test_tracker_waits_for_sell_day_close(self):
        track=module('app.predict.track','app/predict/track.py')
        tr=track.Tracker(self.p['data']);tr.record_prediction({'date':'2026-09-10','targets':[{'code':'600001'}]})
        cached=Mock()
        with self.clock('2026-09-14T09:10:00'):
            result=tr.settle_pending(cached)
        self.assertIn('15:30',result['note']);cached.call.assert_not_called()
        self.assertEqual(result['buy_date'],'2026-09-11')
        self.assertEqual(result['sell_date'],'2026-09-14')

    def test_tracker_mid_autumn_execution_dates(self):
        track=module('app.predict.track','app/predict/track.py')
        tr=track.Tracker(self.p['data']);tr.record_prediction({'date':'2026-09-24','targets':[{'code':'600001'}]})
        ths=types.ModuleType('app.ths_client')
        client=Mock()
        client.kline.side_effect=lambda code,a,b:[{'date_ms':int(datetime(a.year,a.month,a.day,12).timestamp()*1000),'open_price':10,'close_price':11}]
        ths.get_ths_client=lambda:client;sys.modules[ths.__name__]=ths
        tr.settle=Mock(return_value={'settled':1})
        with self.clock('2026-09-29T16:00:00'):
            result=tr.settle_pending(Mock())
        self.assertEqual(result['settled'],1)
        self.assertEqual(tr.settle.call_args.kwargs['buy_date'],'2026-09-28')
        self.assertEqual(tr.settle.call_args.kwargs['sell_date'],'2026-09-29')

    def test_manual_bot_success_records_receipt_but_failure_does_not(self):
        # Execute the actual bot functions; SDK/network/collector are external boundaries replaced by fakes.
        import ast
        source=ast.parse((ROOT/'app/feishu/bot.py').read_text())
        names={'_full_report_and_reply','_full_report_and_reply_locked'}
        nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names]
        ns={'__name__':'app.feishu.bot','__package__':'app.feishu','lark':types.SimpleNamespace(Client=object),
            'paths':lambda:self.p,'logger':logging.getLogger('bot-test'),'reply_text':Mock(),'reply_card':Mock(return_value=True),
            '_send_report_images':Mock(),'execution_plan_text':str,
            'report_link':lambda day:{'public':'','ip':'http://example.invalid','local':'http://example.invalid','ip_addr':'127.0.0.1'}}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),ns)
        report=self.report();report.update(market_index={},emotion={})
        wf=types.ModuleType('app.workflow');wf.run_review=Mock(return_value=report);sys.modules[wf.__name__]=wf
        validator=types.ModuleType('app.report_validator');validator.validate_and_block=lambda r:(True,[])
        sys.modules[validator.__name__]=validator
        with self.clock('2026-09-14T20:30:00'):
            ns['_full_report_and_reply'](None,'msg1',chat_id='test')
            ns['_full_report_and_reply'](None,'msg2',chat_id='test')
            self.auto.run_auto_review()
        self.assertEqual(ns['reply_card'].call_count,2)  # explicit requests always receive their own reply
        self.health.assert_not_called()
        for fp in self.p['data'].glob('*'): fp.unlink()
        ns['reply_card'].return_value=False
        with self.assertLogs('bot-test',level='ERROR'):
            ns['_full_report_and_reply'](None,'msg3',chat_id='test')
        self.assertFalse(self.auto.report_sent('2026-09-14'))

    def test_workflow_morning_reads_saved_friday_without_collecting(self):
        import ast
        d=self.report('2026-09-11')
        source=ast.parse((ROOT/'app/workflow.py').read_text())
        nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='run_review']
        storage=Mock();storage.load_report.return_value=d
        collector=Mock()
        ns={'__name__':'app.workflow','__package__':'app','paths':lambda:self.p,'Storage':Mock(return_value=storage),
            'log':logging.getLogger('workflow-test'),'Collector':collector}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'workflow.py','exec'),ns)
        rules=types.ModuleType('app.rules');rules.preload_rules=Mock(return_value={});sys.modules[rules.__name__]=rules
        with self.clock('2026-09-14T09:10:00'):
            result=ns['run_review']()
        self.assertEqual(result,d);collector.assert_not_called();storage.load_report.assert_called_once_with('2026-09-11')

    def test_lock_excludes_separate_process(self):
        import subprocess
        from app.review_delivery import delivery_lock
        code="from app.review_delivery import delivery_lock; import sys\nwith delivery_lock(sys.argv[1], blocking=False) as got: print(got)"
        with delivery_lock(self.p['data']):
            p=subprocess.run([sys.executable,'-c',code,str(self.p['data'])],cwd=ROOT,capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'False')

    def test_evening_manual_workflow_keeps_original_targets(self):
        import ast
        d=self.report();pred=d['prediction'];pred.update(strategy='fixed',top_sectors=[])
        nodes=[n for n in ast.parse((ROOT/'app/workflow.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='run_review']
        import copy
        midday=copy.deepcopy(d);midday['meta']['generated_at']='2026-09-14T10:00:00'
        storage=Mock();storage.load_report.return_value=midday
        cached=Mock();cached.call.return_value={'data':{'points':[{'time':'2026-09-14'}]}}
        collector=Mock()
        ns={'__name__':'app.workflow','__package__':'app','paths':lambda:self.p,'Storage':Mock(return_value=storage),
            'log':logging.getLogger('workflow-test'),'Collector':collector,'build_report':lambda c:d,
            '_get_cached_mcp':lambda:cached,'_attach_compliance':lambda r,p:r,'_build_tracking':lambda *a,**k:{},'render_html':lambda r:'test','_report_ok':lambda r:True}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'workflow.py','exec'),ns)
        def stub(name,**attrs):
            m=types.ModuleType(name)
            for k,v in attrs.items(): setattr(m,k,v)
            sys.modules[name]=m
        tracker=Mock();tracker.get_prediction.return_value=pred
        predict=Mock(side_effect=AssertionError('已有预测不得重新选股'))
        stub('app.rules',preload_rules=Mock(return_value={}))
        stub('app.predict.cache',MCPCache=Mock(),CachedMcp=Mock())
        stub('app.predict.backtest',Backtest=Mock(),INDEX_TICKER='index')
        stub('app.predict.track',Tracker=Mock(return_value=tracker))
        stub('app.predict.daily',predict=predict)
        stub('app.report_validator',validate_and_block=lambda r:(True,[]))
        stub('app.review_logger',log_review_run=Mock())
        with self.clock('2026-09-14T20:30:00'):
            result=ns['run_review']()
        self.assertEqual(result['prediction']['targets'],pred['targets'])
        collector.return_value.collect.assert_called_once();predict.assert_not_called()
        tracker.settle_pending.assert_called_once()

if __name__=='__main__': unittest.main()
