from sqlalchemy import select,func
from fastapi import HTTPException
from .models import Audit,Lot,Accession,StockMovement

def pack(x):return {c.name:getattr(x,c.name) for c in x.__table__.columns}
def lock(s,cls,id):
    x=s.scalar(select(cls).where(cls.id==id).with_for_update().execution_options(populate_existing=True))
    if not x:raise HTTPException(404,'业务记录不存在')
    return x
def audit(s,u,message):s.add(Audit(actor=u.email,action=message))
def add(s,x):s.add(x);s.flush();return pack(x)
def next_seq(s,lot_id):
    # Called only while holding the accession parent lock, so seq is gap-free per lot.
    return int(s.scalar(select(func.max(StockMovement.seq)).where(StockMovement.lot_id==lot_id)) or 0)+1
def move(s,lot,delta,kind,ref=''):
    m=StockMovement(lot_id=lot.id,seq=next_seq(s,lot.id),delta=int(delta),kind=kind,ref=str(ref)[:60])
    s.add(m);s.flush();return m
def total_of(s,accession_id):
    return int(s.scalar(select(func.sum(Lot.available)).where(Lot.accession_id==accession_id)) or 0)
def reserve(s,lot,quantity,kind='deduct',ref=''):
    a=lock(s,Accession,lot.accession_id)
    current=lock(s,Lot,lot.id)
    total=total_of(s,a.id)
    if total<a.minimum:raise HTTPException(409,'种质总量低于最低保底，补足前暂停扣种')
    if quantity>current.available:raise HTTPException(409,'本批种子不足')
    if total-quantity<a.minimum:raise HTTPException(409,'扣种后低于本种质最低保底量')
    current.available-=quantity
    move(s,current,-quantity,kind,ref)
    return {'accession_code':a.code,'before_total':total,'after_total':total-quantity,'minimum':a.minimum,'lot_before':current.available+quantity}
