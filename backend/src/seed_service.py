from sqlalchemy import select,func
from .db import Base,engine,Session
from .models import *
from .auth import hashed

def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('operator','supervisor','viewer'):
            email=role+'@qinghe.demo'
            if not s.scalar(select(User).where(User.email==email)):s.add(User(email=email,role=role,password_hash=hashed('Seeds2026!')))
        a=s.scalar(select(Accession).where(Accession.code=='QH-021'))
        if not a:a=Accession(code='QH-021',crop='青豆',name='青禾早熟青豆',origin='青禾北圃2023采集',minimum=300);s.add(a);s.flush()
        for code,generation,available in [('QH-021-G3',3,960),('QH-021-POLLEN',3,400)]:
            if not s.scalar(select(Lot).where(Lot.code==code)):s.add(Lot(accession_id=a.id,code=code,generation=generation,available=available,stored_months=36,temperature=4,initial_rate=94))
        other=s.scalar(select(Accession).where(Accession.code=='QH-087'))
        if not other:other=Accession(code='QH-087',crop='青豆',name='异源晚熟青豆',origin='南坡异源对照',minimum=300);s.add(other);s.flush()
        if not s.scalar(select(Lot).where(Lot.code=='QH-087-G2')):s.add(Lot(accession_id=other.id,code='QH-087-G2',generation=2,available=800,stored_months=18,temperature=-8,initial_rate=96))
        for i in range(18):
            code=chr(65+i//6)+str(i%6+1)
            if not s.scalar(select(Plot).where(Plot.code==code)):s.add(Plot(code=code,row=i//6,position=i%6,capacity=250))
        s.flush();lot=s.scalar(select(Lot).where(Lot.code=='QH-021-G3'))
        if not s.scalar(select(GerminationTest).where(GerminationTest.code=='GT-SEED-80')):s.add(GerminationTest(lot_id=lot.id,code='GT-SEED-80',sampled=100,germinated=80,state='recorded'))
        # 每批一笔期初流水，之后所有出入库只追加，不覆盖历史。
        for item in s.scalars(select(Lot)):
            if not s.scalar(select(StockMovement).where(StockMovement.lot_id==item.id)):s.add(StockMovement(lot_id=item.id,seq=1,delta=item.available,kind='opening',ref='入藏'))
        # 历史盘点（账实差）只作为风险先验，已结案且不再改动现有余额。
        for code,book,actual in [('ST-LEGACY-01',1000,980),('ST-LEGACY-02',980,975)]:
            if not s.scalar(select(Stocktake).where(Stocktake.code==code)):
                s.add(Stocktake(code=code,lot_id=lot.id,state='closed',book_qty=book,actual=actual,count_seq=0,note='历史盘点账实差',opened_by='supervisor@qinghe.demo',closed_by='supervisor@qinghe.demo',post_delta=0,closing_available=actual,adjustment=0,expected_after=actual,reconciled=True))
        s.commit();return {cls.__tablename__:s.scalar(select(func.count()).select_from(cls))for cls in (User,Accession,Lot,GerminationTest,Regeneration,Plot,Flowering,Pollination,Harvest,Distribution,Stocktake,StockMovement)}
