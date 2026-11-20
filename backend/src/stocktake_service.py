from datetime import datetime
from collections import defaultdict
from sqlalchemy import select,func
from fastapi import HTTPException
from .models import Accession,Lot,Stocktake,StockMovement
from .common import lock,pack,audit,next_seq,total_of,move

# 出入库流水与盘点共用同一把种质父锁，保证调账不会漏算并发流水。
def open_count(s,d,u):
    lot=s.get(Lot,d.lot_id)
    if not lot:raise HTTPException(404,'种批不存在')
    lock(s,Accession,lot.accession_id)
    lot=lock(s,Lot,lot.id)
    if s.scalar(select(Stocktake.id).where(Stocktake.lot_id==lot.id,Stocktake.state=='open')):
        raise HTTPException(409,'该种批已有未结案盘点，请先由主管对账结案')
    seq=int(s.scalar(select(func.max(StockMovement.seq)).where(StockMovement.lot_id==lot.id)) or 0)
    x=Stocktake(code=d.code,lot_id=lot.id,state='open',book_qty=lot.available,actual=d.actual,count_seq=seq,note=d.note,opened_by=u.email)
    s.add(x);s.flush()
    audit(s,u,f'开袋盘点 {d.code}：账面{lot.available}粒 实数{d.actual}粒 流水位#{seq}')
    return pack(x)

def close(s,id,u):
    pre=s.get(Stocktake,id)
    if not pre:raise HTTPException(404,'盘点单不存在')
    lot=s.get(Lot,pre.lot_id)
    a=lock(s,Accession,lot.accession_id)
    lot=lock(s,Lot,lot.id)
    x=lock(s,Stocktake,id)
    # 结案重试直接返回既有结果，不再次调账。
    if x.state=='closed':
        total=total_of(s,a.id)
        return pack(x)|{'total_after':total,'minimum':a.minimum,'below_minimum':total<a.minimum}
    post=int(s.scalar(select(func.sum(StockMovement.delta)).where(StockMovement.lot_id==lot.id,StockMovement.seq>x.count_seq)) or 0)
    expected=x.actual+post
    if expected<0:raise HTTPException(409,'数袋后流水已超出实数，盘点余量为负；请先核对出入库流水')
    closing_available=lot.available
    adjustment=expected-closing_available
    if adjustment:
        lot.available=expected
        move(s,lot,adjustment,'stocktake',x.code)
    total=total_of(s,a.id)
    x.state='closed';x.closing_available=closing_available;x.post_delta=post;x.expected_after=expected
    x.adjustment=adjustment;x.reconciled=(adjustment==0);x.closed_by=u.email;x.closed_at=datetime.utcnow()
    audit(s,u,f'盘点结案 {x.code}：实数{x.actual}+数后流水{post}=应对余量{expected}，账面{closing_available}，调账{adjustment:+d}，种质总量{total}/{a.minimum}')
    # 低于保底也照实入账；reserve() 会在补足前拒绝扣种。
    return pack(x)|{'total_after':total,'minimum':a.minimum,'below_minimum':total<a.minimum}

def replenish(s,lot_id,d,u):
    lot=s.get(Lot,lot_id)
    if not lot:raise HTTPException(404,'种批不存在')
    a=lock(s,Accession,lot.accession_id)
    lot=lock(s,Lot,lot.id)
    lot.available+=d.seeds
    move(s,lot,d.seeds,'replenish',d.note or '补种')
    total=total_of(s,a.id)
    audit(s,u,f'补种入库 {lot.code} +{d.seeds}粒，种质总量{total}/{a.minimum}')
    return {'lot':pack(lot),'total_after':total,'minimum':a.minimum,'below_minimum':total<a.minimum}

def ledger(s,lot_id):
    lot=s.get(Lot,lot_id)
    if not lot:raise HTTPException(404,'种批不存在')
    rows=s.scalars(select(StockMovement).where(StockMovement.lot_id==lot_id).order_by(StockMovement.seq)).all()
    return {'lot':pack(lot),'movements':[pack(m) for m in rows]}

# 短缺风险：历次盘点账实差（短少频次与幅度）+ 出入库频次。无历史标记待抽盘。
def risk(s):
    accs={a.id:a for a in s.scalars(select(Accession))}
    movements=defaultdict(lambda:{'count':0,'inbound':0,'outbound':0})
    for m in s.scalars(select(StockMovement)):
        g=movements[m.lot_id];g['count']+=1
        if m.delta>0:g['inbound']+=1
        elif m.delta<0:g['outbound']+=1
    counts=defaultdict(list)
    for x in s.scalars(select(Stocktake)):counts[x.lot_id].append(x)
    rows=[]
    for lot in s.scalars(select(Lot).order_by(Lot.id)):
        a=accs[lot.accession_id]
        history=counts.get(lot.id,[]);mv=movements.get(lot.id,{'count':0,'inbound':0,'outbound':0})
        n=len(history)
        short_events=sum(1 for x in history if x.book_qty>x.actual)
        short_ratio=sum(max(0,x.book_qty-x.actual)/max(x.book_qty,1) for x in history)/n if n else 0
        freq=min(1,mv['count']/20)
        if n:
            shortage_rate=short_events/n
            # 账实差（短少频次）是主信号，短少幅度次之，出入库频次放大暴露机会。
            score=round(100*(0.60*shortage_rate+0.25*min(1,short_ratio*10)+0.15*freq),1)
            level='high' if score>=60 else 'medium' if score>=30 else 'low'
            status='estimated'
            reason=f'历史盘点{n}次，短少{short_events}次，平均短少幅度{short_ratio*100:.1f}%；累计出入库{mv["count"]}笔（入{mv["inbound"]}/出{mv["outbound"]}）。'
        else:
            score=None;level='unassessed';status='pending_count'
            reason=f'尚无盘点历史，待抽盘建立账实差；累计出入库{mv["count"]}笔（入{mv["inbound"]}/出{mv["outbound"]}），频次越高越优先开袋。'
        rows.append({'lot_id':lot.id,'lot_code':lot.code,'generation':lot.generation,'available':lot.available,
            'accession_id':a.id,'accession_code':a.code,'accession_name':a.name,'minimum':a.minimum,
            'status':status,'risk_level':level,'risk_score':score,
            'stocktake_count':n,'shortage_events':short_events,'avg_shortfall_ratio':round(short_ratio,4),
            'movement_count':mv['count'],'inbound_count':mv['inbound'],'outbound_count':mv['outbound'],'reason':reason})
    order={'high':0,'unassessed':1,'medium':2,'low':3}
    rows.sort(key=lambda r:(order[r['risk_level']],-(r['risk_score'] or 0),-r['movement_count'],r['lot_id']))
    return rows
