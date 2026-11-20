from fastapi import HTTPException
from sqlalchemy import select
from .models import Accession,Lot,GerminationTest,Regeneration
from .common import lock,pack,add,reserve,audit,move

def create_test(s,d,u):
    lot=s.get(Lot,d.lot_id)
    if not lot:raise HTTPException(404,'种批不存在')
    # Physical germination sample consumes seeds under the accession reserve lock.
    snapshot=reserve(s,lot,d.sampled,'germination',d.code)
    x=GerminationTest(**d.model_dump(),state='recorded');result=add(s,x);audit(s,u,'抽样发芽复测 '+d.code);return result

def request(s,d,u):
    test=lock(s,GerminationTest,d.test_id)
    if test.state!='ready' or not test.prediction:raise HTTPException(409,'先完成活力估计，失败评估可重新排队')
    if not test.prediction['renewal_required']:raise HTTPException(409,'实测与估计活力均≥85%，不触发本轮复壮')
    x=Regeneration(**d.model_dump(),lot_id=test.lot_id,vitality_snapshot=test.prediction);audit(s,u,'申请复壮 '+d.code);return add(s,x)

def issue(s,id,u):
    pre=s.get(Regeneration,id)
    if not pre:raise HTTPException(404,'复壮单不存在')
    lot=s.get(Lot,pre.lot_id);lock(s,Accession,lot.accession_id);x=lock(s,Regeneration,id)
    if x.state!='requested':raise HTTPException(409,'只能对待领种复壮单扣种，不可重复领用')
    reserve(s,lot,x.issued,'regeneration',x.code);x.state='issued';audit(s,u,'主管领种复壮 '+x.code);return pack(x)

def cancel(s,id,u):
    pre=s.get(Regeneration,id)
    if not pre:raise HTTPException(404,'复壮单不存在')
    lot=s.get(Lot,pre.lot_id);lock(s,Accession,lot.accession_id);x=lock(s,Regeneration,id)
    from .models import Flowering
    if x.state not in ('requested','issued'):raise HTTPException(409,'仅未下田复壮单可以取消')
    if s.scalar(select(Flowering.id).where(Flowering.regeneration_id==x.id,Flowering.state=='assigned')):raise HTTPException(409,'已分配隔离小区不可直接取消领种')
    if x.state=='issued':
        lot=lock(s,Lot,lot.id);lot.available+=x.issued;move(s,lot,x.issued,'regeneration_cancel',x.code)
    x.state='cancelled';audit(s,u,'取消并退回未下田种子 '+x.code);return pack(x)
