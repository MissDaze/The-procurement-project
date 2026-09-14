from __future__ import annotations

import csv
import base64
import hashlib
import hmac
import io
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .collectors import austender_contracts, austender_live
from .config import ROOT, settings
from .db import SessionLocal, get_db, init_db
from .models import AdminUser, Agency, ClientProfile, CollectionRun, Contract, OpportunityScore, Prospect, ReportRun, Supplier, SystemSetting, Tender
from .reports.generator import generate
from .services.prospecting import calculate_matches, discover_prospects

app=FastAPI(title="NixSec Procurement Intelligence",version="1.0.0")
app.add_middleware(SessionMiddleware,secret_key=settings.session_secret,https_only=False,same_site="lax",max_age=28800)
app.mount("/static",StaticFiles(directory=ROOT/"app"/"web"/"static"),name="static")
templates=Jinja2Templates(directory=ROOT/"app"/"web"/"templates")

def hash_password(password: str) -> str:
    salt=secrets.token_bytes(16); digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
    return "scrypt$"+base64.urlsafe_b64encode(salt).decode()+"$"+base64.urlsafe_b64encode(digest).decode()

def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme,salt64,digest64=encoded.split("$",2)
        if scheme!="scrypt": return False
        salt=base64.urlsafe_b64decode(salt64); expected=base64.urlsafe_b64decode(digest64)
        actual=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
        return hmac.compare_digest(actual,expected)
    except Exception: return False


@app.on_event("startup")
def startup():
    settings.report_dir.mkdir(exist_ok=True); settings.upload_dir.mkdir(exist_ok=True); init_db()
    if settings.admin_email and settings.admin_password:
        with SessionLocal() as db:
            if not db.scalar(select(AdminUser).where(AdminUser.email==settings.admin_email.lower())):
                db.add(AdminUser(email=settings.admin_email.lower(),password_hash=hash_password(settings.admin_password))); db.commit()


def auth(request:Request,db:Session=Depends(get_db)):
    uid=request.session.get("uid")
    user=db.get(AdminUser,uid) if uid else None
    if not user: raise HTTPException(303,headers={"Location":"/login"})
    return user


def ctx(request,**kwargs): return {"request":request,"path":request.url.path,**kwargs}


@app.get("/health")
def health(): return {"status":"ok","time":datetime.now(timezone.utc).isoformat()}


@app.get("/login",response_class=HTMLResponse)
def login_page(request:Request): return templates.TemplateResponse("login.html",ctx(request,error=None))


@app.post("/login")
def login(request:Request,email:str=Form(...),password:str=Form(...),db:Session=Depends(get_db)):
    user=db.scalar(select(AdminUser).where(AdminUser.email==email.lower()))
    if not user or not verify_password(password,user.password_hash): return templates.TemplateResponse("login.html",ctx(request,error="Invalid email or password"),status_code=401)
    request.session.clear(); request.session["uid"]=user.id; return RedirectResponse("/",303)


@app.post("/logout")
def logout(request:Request): request.session.clear(); return RedirectResponse("/login",303)


@app.get("/",response_class=HTMLResponse)
def dashboard(request:Request,db:Session=Depends(get_db),_=Depends(auth)):
    now=datetime.now(timezone.utc); week=now+timedelta(days=7)
    metrics={"live":db.scalar(select(func.count()).select_from(Tender).where(Tender.status.in_(["LIVE","CLOSING SOON"]))) or 0,"new_today":db.scalar(select(func.count()).select_from(Tender).where(Tender.published_at>=datetime.combine(date.today(),datetime.min.time(),tzinfo=timezone.utc))) or 0,"closing":db.scalar(select(func.count()).select_from(Tender).where(Tender.closes_at.between(now,week))) or 0,"contracts":db.scalar(select(func.count()).select_from(Contract)) or 0,"suppliers":db.scalar(select(func.count()).select_from(Supplier)) or 0,"agencies":db.scalar(select(func.count()).select_from(Agency)) or 0,"prospects":db.scalar(select(func.count()).select_from(Prospect)) or 0,"high":db.scalar(select(func.count()).select_from(Prospect).where(Prospect.score>=70)) or 0,"previews":db.scalar(select(func.count()).select_from(ReportRun)) or 0,"clients":db.scalar(select(func.count()).select_from(ClientProfile).where(ClientProfile.active==True)) or 0}
    top=db.scalars(select(Prospect).order_by(Prospect.score.desc()).limit(10)).all(); last=db.scalars(select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(5)).all()
    return templates.TemplateResponse("dashboard.html",ctx(request,metrics=metrics,prospects=top,runs=last,empty=metrics["live"]==0 and metrics["contracts"]==0))


def initialise_job():
    with SessionLocal() as db:
        db.merge(SystemSetting(key="initialisation",value={"stage":"Collecting live tenders","running":True})); db.commit()
        live=austender_live.collect(db)
        db.merge(SystemSetting(key="initialisation",value={"stage":"Collecting historical contracts","running":True,"live_status":live.status})); db.commit()
        contracts=austender_contracts.collect(db)
        db.merge(SystemSetting(key="initialisation",value={"stage":"Discovering and scoring prospects","running":True,"live_status":live.status,"contract_status":contracts.status})); db.commit()
        discover_prospects(db); calculate_matches(db)
        db.merge(SystemSetting(key="initialisation",value={"stage":"Complete","running":False,"live_status":live.status,"contract_status":contracts.status,"finished":datetime.now(timezone.utc).isoformat()})); db.commit()


@app.post("/initialise")
def initialise(_:AdminUser=Depends(auth)):
    threading.Thread(target=initialise_job,daemon=True).start(); return RedirectResponse("/data-collection",303)


@app.post("/collect/live")
def collect_live(background:BackgroundTasks,_:AdminUser=Depends(auth)):
    def job():
        with SessionLocal() as db: austender_live.collect(db)
    background.add_task(job); return RedirectResponse("/data-collection",303)


@app.post("/collect/contracts")
def collect_contracts(background:BackgroundTasks,_:AdminUser=Depends(auth)):
    def job():
        with SessionLocal() as db: austender_contracts.collect(db)
    background.add_task(job); return RedirectResponse("/data-collection",303)


@app.get("/data-collection",response_class=HTMLResponse)
def collection_page(request:Request,db:Session=Depends(get_db),_=Depends(auth)):
    runs=db.scalars(select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(30)).all(); init=db.get(SystemSetting,"initialisation")
    return templates.TemplateResponse("collection.html",ctx(request,runs=runs,initialisation=init.value if init else None))


@app.get("/tenders",response_class=HTMLResponse)
def tenders(request:Request,q:str="",status:str="",db:Session=Depends(get_db),_=Depends(auth)):
    stmt=select(Tender)
    if q: stmt=stmt.where(or_(Tender.title.ilike(f"%{q}%"),Tender.description.ilike(f"%{q}%"),Tender.agency_name.ilike(f"%{q}%"),Tender.source_id.ilike(f"%{q}%")))
    if status: stmt=stmt.where(Tender.status==status)
    rows=db.scalars(stmt.order_by(Tender.closes_at.asc()).limit(500)).all()
    return templates.TemplateResponse("tenders.html",ctx(request,rows=rows,q=q,status=status))


@app.get("/tenders/export.csv")
def tender_export(db:Session=Depends(get_db),_=Depends(auth)):
    out=io.StringIO(); w=csv.writer(out); w.writerow(["ATM ID","Title","Agency","Published","Closes","Status","Category","Source URL"])
    for t in db.scalars(select(Tender).order_by(Tender.closes_at)).all(): w.writerow([t.source_id,t.title,t.agency_name,t.published_at,t.closes_at,t.status,t.category,t.source_url])
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=nixsec-live-tenders.csv"})


@app.get("/prospects",response_class=HTMLResponse)
def prospects(request:Request,q:str="",min_score:int=0,stage:str="",db:Session=Depends(get_db),_=Depends(auth)):
    stmt=select(Prospect).join(Supplier).where(Prospect.score>=min_score)
    if q: stmt=stmt.where(Supplier.canonical_name.ilike(f"%{q}%"))
    if stage: stmt=stmt.where(Prospect.lifecycle_stage==stage)
    rows=db.scalars(stmt.order_by(Prospect.score.desc()).limit(500)).all()
    return templates.TemplateResponse("prospects.html",ctx(request,rows=rows,q=q,min_score=min_score,stage=stage))


@app.get("/prospects/{prospect_id}",response_class=HTMLResponse)
def prospect_detail(request:Request,prospect_id:int,db:Session=Depends(get_db),_=Depends(auth)):
    p=db.get(Prospect,prospect_id)
    if not p: raise HTTPException(404)
    contracts=db.scalars(select(Contract).where(Contract.supplier_id==p.supplier_id).order_by(Contract.publication_date.desc()).limit(50)).all()
    matches=db.execute(select(OpportunityScore,Tender).join(Tender).where(OpportunityScore.prospect_id==p.id).order_by(OpportunityScore.score.desc())).all()
    client=db.scalar(select(ClientProfile).where(ClientProfile.prospect_id==p.id))
    return templates.TemplateResponse("prospect.html",ctx(request,p=p,contracts=contracts,matches=matches,client=client))


@app.post("/prospects/{prospect_id}/stage")
def set_stage(prospect_id:int,stage:str=Form(...),db:Session=Depends(get_db),_=Depends(auth)):
    p=db.get(Prospect,prospect_id); p.lifecycle_stage=stage; db.commit(); return RedirectResponse(f"/prospects/{prospect_id}",303)


@app.post("/prospects/{prospect_id}/convert")
def convert(prospect_id:int,db:Session=Depends(get_db),_=Depends(auth)):
    p=db.get(Prospect,prospect_id)
    if not p: raise HTTPException(404)
    client=db.scalar(select(ClientProfile).where(ClientProfile.prospect_id==p.id))
    if not client: db.add(ClientProfile(prospect_id=p.id,company_name=p.supplier.canonical_name,abn=p.supplier.abn,industry=(p.main_categories or [None])[0],capabilities=p.inferred_capabilities or [],keywords=p.inferred_capabilities or []))
    p.lifecycle_stage="Client"; db.commit(); return RedirectResponse(f"/prospects/{prospect_id}",303)


@app.get("/clients",response_class=HTMLResponse)
def clients(request:Request,db:Session=Depends(get_db),_=Depends(auth)):
    return templates.TemplateResponse("clients.html",ctx(request,rows=db.scalars(select(ClientProfile).order_by(ClientProfile.company_name)).all()))


@app.get("/contracts",response_class=HTMLResponse)
def contracts(request:Request,q:str="",db:Session=Depends(get_db),_=Depends(auth)):
    stmt=select(Contract)
    if q: stmt=stmt.where(or_(Contract.title.ilike(f"%{q}%"),Contract.source_id.ilike(f"%{q}%")))
    return templates.TemplateResponse("contracts.html",ctx(request,rows=db.scalars(stmt.order_by(Contract.end_date.asc()).limit(500)).all(),q=q))


@app.post("/reports/generate/{prospect_id}")
def report_generate(prospect_id:int,report_type:str=Form("Two-Page Prospect Preview"),db:Session=Depends(get_db),_=Depends(auth)):
    run=generate(db,prospect_id,report_type); return RedirectResponse(f"/reports/{run.report_id}/download",303)


@app.get("/reports",response_class=HTMLResponse)
def reports(request:Request,db:Session=Depends(get_db),_=Depends(auth)):
    return templates.TemplateResponse("reports.html",ctx(request,rows=db.scalars(select(ReportRun).order_by(ReportRun.created_at.desc())).all()))


@app.get("/reports/{report_id}/download")
def report_download(report_id:str,db:Session=Depends(get_db),_=Depends(auth)):
    run=db.scalar(select(ReportRun).where(ReportRun.report_id==report_id))
    if not run or not Path(run.file_path).exists(): raise HTTPException(404)
    return FileResponse(run.file_path,media_type="application/pdf",filename=f"{report_id}.pdf")


@app.get("/search",response_class=HTMLResponse)
def search_all(request:Request,q:str,db:Session=Depends(get_db),_=Depends(auth)):
    ts=db.scalars(select(Tender).where(or_(Tender.title.ilike(f"%{q}%"),Tender.agency_name.ilike(f"%{q}%"))).limit(30)).all(); ps=db.scalars(select(Prospect).join(Supplier).where(Supplier.canonical_name.ilike(f"%{q}%")).limit(30)).all(); cs=db.scalars(select(Contract).where(Contract.title.ilike(f"%{q}%")).limit(30)).all()
    return templates.TemplateResponse("search.html",ctx(request,q=q,tenders=ts,prospects=ps,contracts=cs))
