import os, sqlite3, asyncio, hashlib, secrets, html
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "7687242263") or 7687242263)
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
DB_PATH = os.getenv("DATABASE_PATH", "/data/easytask.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, country TEXT DEFAULT 'Unknown', joined TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, platform TEXT, description TEXT, rate REAL, currency TEXT, active INTEGER DEFAULT 1, created TEXT);
        CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_id INTEGER, proof TEXT, status TEXT DEFAULT 'pending', created TEXT, reviewed TEXT);
        CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, currency TEXT, details TEXT, status TEXT DEFAULT 'pending', created TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        defaults = {"inr_rate":"10", "usdt_rate":"0.12", "usdt_wallet":"", "upi_id":""}
        for k,v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))

def setting(k):
    with db() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone()
        return r["value"] if r else ""

def now():
    return datetime.now(timezone.utc).isoformat()

def is_admin(uid):
    return ADMIN_ID and uid == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(id,username,joined) VALUES(?,?,?)",(u.id,u.username or "",now()))
        c.execute("UPDATE users SET username=? WHERE id=?",(u.username or "",u.id))
    kb=ReplyKeyboardMarkup([["👤 Profile","🐱 Tasks"],["💸 Withdraw","🆘 Support"],["🤝 My Referral","📑 How to Work"]],resize_keyboard=True)
    await update.message.reply_text(
        "🎉 Welcome to EASYTASK!\n\nComplete authorized tasks, submit proof and track review status.\n\nChoose an option below.",
        reply_markup=kb)

async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows=c.execute("SELECT id,title,platform,rate,currency FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    if not rows:
        await update.effective_message.reply_text("No tasks are available right now. Please check again later.")
        return
    for t in rows:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("Submit task proof",callback_data=f"task:{t['id']}")]])
        await update.effective_message.reply_text(
            f"📋 {t['title']}\nPlatform: {t['platform']}\nReward: {t['rate']} {t['currency']}",
            reply_markup=kb)

async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    tid=int(q.data.split(":")[1])
    with db() as c:
        t=c.execute("SELECT * FROM tasks WHERE id=? AND active=1",(tid,)).fetchone()
    if not t:
        await q.message.reply_text("This task is no longer available.")
        return
    context.user_data["awaiting_proof"]=tid
    await q.message.reply_text("Send your task reference and a short proof description. Do not send passwords, cookies, session tokens or login credentials.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=(update.message.text or "").strip()
    uid=update.effective_user.id
    if msg in ("🐱 Tasks","Task","Tasks"):
        await tasks(update,context); return
    if msg in ("👤 Profile","Profile"):
        with db() as c:
            u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
            total=c.execute("SELECT COUNT(*) n FROM submissions WHERE user_id=?",(uid,)).fetchone()["n"]
            done=c.execute("SELECT COUNT(*) n FROM submissions WHERE user_id=? AND status='approved'",(uid,)).fetchone()["n"]
        await update.message.reply_text(f"👤 Profile\nID: {uid}\nUsername: @{u['username'] or '—'}\nTasks submitted: {total}\nApproved: {done}")
        return
    if msg in ("📑 How to Work","How to work"):
        await update.message.reply_text("1. Open Tasks\n2. Choose an available task\n3. Complete the authorized activity\n4. Submit a task reference and proof\n5. Wait for admin review.")
        return
    if msg in ("🆘 Support","Support"):
        await update.message.reply_text("For support, contact the bot administrator.")
        return
    if msg in ("🤝 My Referral","My Referral"):
        await update.message.reply_text(f"Your referral link:\nhttps://t.me/{context.bot.username}?start={uid}\nReferral rewards are subject to admin settings.")
        return
    if msg.startswith("💸 Withdraw") or msg=="Withdraw":
        await update.message.reply_text("Send withdrawal request in this format:\nAMOUNT | CURRENCY | PAYMENT DETAILS\nExample: 100 | INR | your-UPI-ID\nNever send wallet seed phrases or private keys.")
        context.user_data["awaiting_withdraw"]=True
        return
    if context.user_data.pop("awaiting_withdraw",False):
        try:
            amount,currency,details=[x.strip() for x in msg.split("|",2)]
            amount=float(amount)
            if amount<=0 or currency.upper() not in ("INR","USDT") or len(details)>180:
                raise ValueError()
            with db() as c:
                c.execute("INSERT INTO withdrawals(user_id,amount,currency,details,created) VALUES(?,?,?,?,?)",(uid,amount,currency.upper(),details,now()))
            await update.message.reply_text("✅ Withdrawal request submitted for admin review.")
            if ADMIN_ID:
                await context.bot.send_message(ADMIN_ID,f"New withdrawal request from {uid}: {amount} {currency.upper()}")
        except Exception:
            await update.message.reply_text("Invalid format. Use: AMOUNT | INR/USDT | PAYMENT DETAILS")
        return
    tid=context.user_data.pop("awaiting_proof",None)
    if tid:
        if len(msg)>1000:
            await update.message.reply_text("Please keep proof under 1000 characters.")
            return
        with db() as c:
            c.execute("INSERT INTO submissions(user_id,task_id,proof,created) VALUES(?,?,?,?)",(uid,tid,msg,now()))
        await update.message.reply_text("✅ Proof submitted. You will be notified after review.")
        if ADMIN_ID:
            with db() as c:
                sid=c.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            await context.bot.send_message(ADMIN_ID,f"New task proof #{sid}\nUser: {uid}\nTask: {tid}\nProof: {msg[:700]}")
        return
    await update.message.reply_text("Choose an option from the menu or send /tasks.")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("Admin panel: open the secure web dashboard URL configured on Railway.")

async def notify_review(context: ContextTypes.DEFAULT_TYPE, user_id: int, status: str, sid: int):
    await context.bot.send_message(user_id,f"Your task submission #{sid} was {status}.")

bot_app=None
@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    init_db()
    if BOT_TOKEN:
        bot_app=Application.builder().token(BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start",start))
        bot_app.add_handler(CommandHandler("tasks",tasks))
        bot_app.add_handler(CommandHandler("admin",admin_command))
        bot_app.add_handler(CallbackQueryHandler(task_callback,pattern=r"^task:\d+$"))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
    yield
    if bot_app:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()

app=FastAPI(title="EASYTASK Admin",lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=SECRET_KEY,same_site="lax",https_only=True)

def page(body, title="EASYTASK Admin"):
    return HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{title}</title><style>
    body{{margin:0;background:#0d1020;color:#eef;font:15px Arial}}header{{padding:20px;background:#17172d;display:flex;justify-content:space-between}}
    main{{max-width:1000px;margin:22px auto;padding:0 14px}}.card{{background:#191d33;border:1px solid #303653;border-radius:14px;padding:16px;margin:12px 0}}
    input,textarea,select{{box-sizing:border-box;width:100%;padding:12px;margin:6px 0 12px;background:#0f1428;color:white;border:1px solid #414968;border-radius:8px}}
    button,.btn{{display:inline-block;background:#6552d9;color:white;border:0;padding:11px 15px;border-radius:8px;text-decoration:none;margin:4px 2px;cursor:pointer}}
    .green{{background:#138b51}}.red{{background:#b7354b}}a{{color:#b9aaff}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #353b55;text-align:left;word-break:break-word}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}small{{color:#aab}}</style></head><body>
    <header><b>⚡ EASYTASK ADMIN</b><a href="/logout">Logout</a></header><main>{body}</main></body></html>""")

def require_login(request):
    if not request.session.get("ok"):
        raise HTTPException(401,"Please log in at /login")

@app.get("/",response_class=HTMLResponse)
def dashboard(request: Request):
    require_login(request)
    with db() as c:
        users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        tasks_n=c.execute("SELECT COUNT(*) n FROM tasks").fetchone()["n"]
        pending=c.execute("SELECT COUNT(*) n FROM submissions WHERE status='pending'").fetchone()["n"]
        withdrawals=c.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
    body=f"""<h2>Dashboard</h2><div class="grid">
    <div class="card">👥<h2>{users}</h2><small>Total users</small></div><div class="card">📋<h2>{tasks_n}</h2><small>Total tasks</small></div>
    <div class="card">⏳<h2>{pending}</h2><small>Pending proofs</small></div><div class="card">💸<h2>{withdrawals}</h2><small>Pending withdrawals</small></div></div>
    <div class="card"><a class="btn" href="/tasks">Tasks</a><a class="btn" href="/submissions">Reports</a><a class="btn" href="/withdrawals">Withdrawals</a><a class="btn" href="/settings">Rates & Wallet</a><a class="btn" href="/broadcast">Broadcast</a><a class="btn" href="/export">Export CSV</a></div>"""
    return page(body)

@app.get("/login",response_class=HTMLResponse)
def login_page():
    return page("""<div class="card"><h2>Admin Login</h2><form method="post"><label>Panel password</label><input type="password" name="password" required><button>Login</button></form></div>""")

@app.post("/login")
def login(request: Request,password:str=Form(...)):
    if not PANEL_PASSWORD or not secrets.compare_digest(password,PANEL_PASSWORD):
        return page('<div class="card">Invalid password. Set PANEL_PASSWORD on Railway.</div>')
    request.session["ok"]=True
    return RedirectResponse("/",status_code=303)

@app.get("/logout")
def logout(request:Request):
    request.session.clear()
    return RedirectResponse("/login",status_code=303)

@app.get("/tasks",response_class=HTMLResponse)
def task_page(request:Request):
    require_login(request)
    with db() as c: rows=c.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    trs="".join(f"<tr><td>{r['id']}</td><td>{html.escape(r['title'])}</td><td>{html.escape(r['platform'])}</td><td>{r['rate']} {r['currency']}</td><td>{'Active' if r['active'] else 'Off'}</td><td><form method='post' action='/tasks/{r['id']}/toggle'><button>Toggle</button></form></td></tr>" for r in rows)
    body=f"""<h2>Task Management</h2><div class="card"><form method="post"><input name="title" placeholder="Task title" required><input name="platform" placeholder="Platform/category" required><textarea name="description" placeholder="Instructions" required></textarea><input name="rate" type="number" step="0.01" min="0" placeholder="Reward amount" required><select name="currency"><option>INR</option><option>USDT</option></select><button class="green">Add task</button></form></div><div class="card"><table><tr><th>ID</th><th>Title</th><th>Platform</th><th>Rate</th><th>Status</th><th>Action</th></tr>{trs}</table></div><a href="/">Back</a>"""
    return page(body)

@app.post("/tasks")
def add_task(request:Request,title:str=Form(...),platform:str=Form(...),description:str=Form(...),rate:float=Form(...),currency:str=Form(...)):
    require_login(request)
    if rate<0 or currency not in ("INR","USDT"): raise HTTPException(400,"Invalid rate")
    with db() as c:c.execute("INSERT INTO tasks(title,platform,description,rate,currency,created) VALUES(?,?,?,?,?,?)",(title[:100],platform[:60],description[:1000],rate,currency,now()))
    return RedirectResponse("/tasks",303)

@app.post("/tasks/{tid}/toggle")
def toggle_task(request:Request,tid:int):
    require_login(request)
    with db() as c:c.execute("UPDATE tasks SET active=1-active WHERE id=?",(tid,))
    return RedirectResponse("/tasks",303)

@app.get("/submissions",response_class=HTMLResponse)
def submissions_page(request:Request):
    require_login(request)
    with db() as c: rows=c.execute("SELECT s.*,t.title FROM submissions s LEFT JOIN tasks t ON t.id=s.task_id ORDER BY s.id DESC LIMIT 200").fetchall()
    parts=[]
    for r in rows:
        buttons = f'<form style="display:inline" method="post" action="/submissions/{r["id"]}/approve"><button class="green">approve</button></form> <form style="display:inline" method="post" action="/submissions/{r["id"]}/reject"><button class="red">reject</button></form>' 
        parts.append(f'<tr><td>{r["id"]}</td><td>{r["user_id"]}</td><td>{html.escape(r["title"] or "")}</td><td>{html.escape(r["proof"])}</td><td>{r["status"]}</td><td>{buttons}</td></tr>')
    trs="".join(parts)
    return page(f"<h2>Task Reports</h2><div class='card'><table><tr><th>ID</th><th>User</th><th>Task</th><th>Proof</th><th>Status</th><th>Review</th></tr>{trs}</table></div><a href='/'>Back</a>")

@app.post("/submissions/{sid}/{decision}")
async def review_submission(request:Request,sid:int,decision:str):
    require_login(request)
    if decision not in ("approve","reject"): raise HTTPException(400,"Invalid decision")
    status="approved" if decision=="approve" else "rejected"
    with db() as c:
        r=c.execute("SELECT user_id FROM submissions WHERE id=?",(sid,)).fetchone()
        if not r: raise HTTPException(404,"Not found")
        c.execute("UPDATE submissions SET status=?,reviewed=? WHERE id=?",(status,now(),sid))
    if bot_app: await bot_app.bot.send_message(r["user_id"],f"Your task submission #{sid} was {status}.")
    return RedirectResponse("/submissions",303)

@app.get("/withdrawals",response_class=HTMLResponse)
def withdrawals_page(request:Request):
    require_login(request)
    with db() as c: rows=c.execute("SELECT * FROM withdrawals ORDER BY id DESC LIMIT 200").fetchall()
    trs="".join(f"<tr><td>{r['id']}</td><td>{r['user_id']}</td><td>{r['amount']} {r['currency']}</td><td>{html.escape(r['details'])}</td><td>{r['status']}</td><td><form method='post' action='/withdrawals/{r['id']}/approve'><button class='green'>Mark paid</button></form><form method='post' action='/withdrawals/{r['id']}/reject'><button class='red'>Reject</button></form></td></tr>" for r in rows)
    return page(f"<h2>Withdrawals</h2><div class='card'><table><tr><th>ID</th><th>User</th><th>Amount</th><th>Details</th><th>Status</th><th>Action</th></tr>{trs}</table></div><a href='/'>Back</a>")

@app.post("/withdrawals/{wid}/{decision}")
def review_withdrawal(request:Request,wid:int,decision:str):
    require_login(request)
    if decision not in ("approve","reject"): raise HTTPException(400,"Invalid decision")
    with db() as c:c.execute("UPDATE withdrawals SET status=? WHERE id=?",("paid" if decision=="approve" else "rejected",wid))
    return RedirectResponse("/withdrawals",303)

@app.get("/settings",response_class=HTMLResponse)
def settings_page(request:Request):
    require_login(request)
    return page(f"""<h2>Rates & Payment Settings</h2><div class="card"><form method="post">
    <label>INR task rate</label><input name="inr_rate" value="{html.escape(setting('inr_rate'))}">
    <label>USDT task rate</label><input name="usdt_rate" value="{html.escape(setting('usdt_rate'))}">
    <label>UPI ID</label><input name="upi_id" value="{html.escape(setting('upi_id'))}">
    <label>USDT BEP20 public wallet address</label><input name="usdt_wallet" value="{html.escape(setting('usdt_wallet'))}">
    <button>Save settings</button></form></div><a href='/'>Back</a>""")

@app.post("/settings")
def save_settings(request:Request,inr_rate:str=Form(...),usdt_rate:str=Form(...),upi_id:str=Form(""),usdt_wallet:str=Form("")):
    require_login(request)
    for k,v in {"inr_rate":inr_rate,"usdt_rate":usdt_rate,"upi_id":upi_id,"usdt_wallet":usdt_wallet}.items():
        with db() as c:c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v[:200]))
    return RedirectResponse("/settings",303)

@app.get("/broadcast",response_class=HTMLResponse)
def broadcast_page(request:Request):
    require_login(request)
    return page("""<h2>Broadcast</h2><div class="card"><form method="post"><textarea name="message" required placeholder="Announcement to all users"></textarea><button>Send broadcast</button></form></div><a href='/'>Back</a>""")

@app.post("/broadcast",response_class=HTMLResponse)
async def broadcast(request:Request,message:str=Form(...)):
    require_login(request)
    if not bot_app: return page("Bot is not configured. Set BOT_TOKEN on Railway.")
    with db() as c: ids=[r["id"] for r in c.execute("SELECT id FROM users").fetchall()]
    sent=failed=0
    for uid in ids:
        try: await bot_app.bot.send_message(uid,message[:4000]); sent+=1
        except Exception: failed+=1
        await asyncio.sleep(0.04)
    return page(f"<div class='card'><h2>Broadcast finished</h2>Sent: {sent}<br>Failed: {failed}</div><a href='/'>Back</a>")

@app.get("/export")
def export_csv(request:Request):
    require_login(request)
    with db() as c: rows=c.execute("SELECT s.id,s.user_id,s.task_id,t.title,s.proof,s.status,s.created FROM submissions s LEFT JOIN tasks t ON t.id=s.task_id ORDER BY s.id").fetchall()
    import csv,io
    out=io.StringIO(); w=csv.writer(out); w.writerow(["submission_id","telegram_user_id","task_id","task_title","proof","status","created"])
    for r in rows:w.writerow(list(r))
    return PlainTextResponse(out.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=easytask_reports.csv"})

@app.get("/health")
def health():
    return {"ok":True,"bot_configured":bool(BOT_TOKEN)}
