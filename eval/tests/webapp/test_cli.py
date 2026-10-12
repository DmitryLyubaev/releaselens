import json
from app.webapp import __main__ as cli
from app.webapp.contracts import Observation
from app.webapp.private_az import TransportRefused
from app.gateway.freeze import DirtyTreeError
from .test_checks import SHA

def test_dirty_tree_precedes_any_environment_cookie_or_network(tmp_path,capsys):
    class Secrets:
        def get(self,*_): raise AssertionError('Secrets read')
        def __getitem__(self,_): raise AssertionError('Secrets read')
    def dirty(): raise DirtyTreeError('private marker')
    assert cli.main(['owner'],env=Secrets(),clean_guard=dirty,out=tmp_path)==1
    assert not list(tmp_path.iterdir()) and capsys.readouterr().out=='status=refused\n'

def test_measurement_failure_writes_failed_schema_without_private_error(tmp_path,capsys):
    def failed(_): raise TransportRefused('network')
    assert cli.main(['owner'],env={},clean_guard=lambda:None,output_guard=lambda _:None,out=tmp_path,commit_reader=lambda:SHA,context_factory=lambda *args:object(),operations={'owner':failed})==1
    data=json.loads((tmp_path/'check-w1.json').read_text())
    assert data['verdict']=='fail' and data['commit']==SHA
    assert capsys.readouterr().out=='status=fail\n'

def test_report_missing_files_is_offline_and_not_run(tmp_path,capsys):
    assert cli.main(['report'],env={},clean_guard=lambda:None,output_guard=lambda _:None,out=tmp_path,commit_reader=lambda:SHA,context_factory=lambda *args:(_ for _ in ()).throw(AssertionError('network')))==0
    assert '| W1 | not run | none |' in (tmp_path/'report.md').read_text()

def test_cross_commit_file_refuses_report(tmp_path,capsys):
    (tmp_path/'check-w1.json').write_text(json.dumps(dict(check='W1',verdict='fail',evidence='live',observations=[],commit='b'*40,measured_at='2026-10-12T01:00:00Z')))
    assert cli.main(['report'],env={},clean_guard=lambda:None,output_guard=lambda _:None,out=tmp_path,commit_reader=lambda:SHA)==1
    assert not (tmp_path/'report.md').exists()

def test_callable_cli_restores_the_callers_logging_policy_after_private_operation(tmp_path):
    import logging
    from app.webapp import release_cli
    before=(logging.root.manager.disable,logging.lastResort)
    assert cli.main(['unknown'],clean_guard=lambda:None,output_guard=lambda _:None,out=tmp_path)==1
    assert (logging.root.manager.disable,logging.lastResort)==before
    assert release_cli.main(['unknown'],env={},clean_guard=lambda:None,output_guard=lambda _:None,root=tmp_path)==1
    assert (logging.root.manager.disable,logging.lastResort)==before
