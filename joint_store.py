"""Shaft-joint cases in their own SQLite file; cycle/manager cases are untouched."""
import json
if __package__:
    from .storage import CaseStore
else:
    from storage import CaseStore


class JointStore(CaseStore):
    def list(self):
        with self.connect() as db:
            results=[]
            for row in db.execute('SELECT * FROM cases ORDER BY rowid DESC'):
                case=json.loads(row['document']); r=case['result']
                results.append({'id':row['id'],'name':case['input']['name'],'kind':case['input']['kind'],
                                'created_at':row['created_at'],'status':r['status'],
                                'torque_n_m':r['nominal_torque_n_m'],'maximum_utilization':r['maximum_utilization']})
            return results
