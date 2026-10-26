"""Koordinatorlar platformasi. Python 3.10+, aiohttp HTTP server va Telegram polling."""
import json
import asyncio
import signal
import threading
from aiohttp import web
import os
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=5))
REGIONS = ["Andijon", "Buxoro", "Farg‘ona", "Jizzax", "Namangan", "Navoiy", "Qashqadaryo", "Qoraqalpog‘iston Respublikasi", "Samarqand", "Sirdaryo", "Surxondaryo", "Toshkent viloyati", "Toshkent shahri", "Xorazm"]
MENU = [["📋 Topshiriqlar", "📤 Hisobot yuborish"], ["👤 Profil", "💡 Taklif"], ["⚙️ Boshqaruv", "❌ Bekor qilish"]]

def load_env():
    p = ROOT / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

class Telegram:
    def __init__(self, token):
        self.base = 'https://api.telegram.org/bot' + token + '/'
    def call(self, method, **data):
        req = urllib.request.Request(self.base + method, json.dumps(data).encode(), {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as e:
            raise RuntimeError('Telegram HTTP ' + str(e.code)) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError('Telegram bilan aloqa yo‘q') from None
        if not result.get('ok'):
            raise RuntimeError('Telegram so‘rovni qabul qilmadi')
        return result['result']

class App:
    def __init__(self, db, api, owner, directions):
        self.db = sqlite3.connect(db)
        self.db.row_factory = sqlite3.Row
        self.api, self.owner, self.directions = api, owner, directions
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,region TEXT,district TEXT,school TEXT,direction TEXT,role TEXT DEFAULT 'member',approved INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY,kind TEXT,data TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,creator INTEGER,title TEXT,due TEXT,region TEXT,district TEXT,direction TEXT);
        CREATE TABLE IF NOT EXISTS reports(task INTEGER,uid INTEGER,body TEXT,file TEXT,kind TEXT,status TEXT DEFAULT 'Kutilmoqda',note TEXT DEFAULT '',PRIMARY KEY(task,uid));
        CREATE TABLE IF NOT EXISTS suggestions(id INTEGER PRIMARY KEY,uid INTEGER,body TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS reminders(task INTEGER,uid INTEGER,PRIMARY KEY(task,uid));
        ''')
        self.db.execute("INSERT OR IGNORE INTO users VALUES(?, 'Bosh administrator','','','','','owner',1)", (owner,))
        self.db.execute("UPDATE users SET role='member',approved=0 WHERE role='owner' AND id<>?", (owner,))
        self.db.execute("UPDATE users SET role='owner',approved=1 WHERE id=?", (owner,))
        self.db.commit()
    def one(self, sql, args=()): return self.db.execute(sql, args).fetchone()
    def rows(self, sql, args=()): return self.db.execute(sql, args).fetchall()
    def write(self, sql, args=()):
        cur = self.db.execute(sql, args); self.db.commit(); return cur.lastrowid
    def send(self, uid, text, keyboard=None):
        kb = keyboard if keyboard is not None else MENU
        for start in range(0, len(text), 3800):
            self.api.call('sendMessage', chat_id=uid, text=text[start:start+3800], reply_markup={'keyboard': kb, 'resize_keyboard': True})
    def notify(self, uid, text):
        try: self.send(uid, text)
        except RuntimeError: pass  # Ma'lumot bazada qoladi, menyudan ko'rish mumkin.
    def session(self, uid, kind, data):
        self.write('INSERT OR REPLACE INTO sessions VALUES(?,?,?)', (uid, kind, json.dumps(data)))
    def clear(self, uid): self.write('DELETE FROM sessions WHERE id=?', (uid,))
    def user(self, uid): return self.one('SELECT * FROM users WHERE id=?', (uid,))
    def manager(self, u): return u and u['approved'] and u['role'] in ('owner','region','district')
    def manages(self, u, other):
        return self.manager(u) and (u['role']=='owner' or (u['region']==other['region'] and (u['role']=='region' or u['district']==other['district'])))
    def visible(self, u, t):
        if not u or not u['approved']: return False
        if u['role']=='owner': return True
        return (not t['region'] or t['region']==u['region']) and (not t['district'] or t['district']==u['district']) and (not t['direction'] or t['direction']==u['direction'])
    def reviewable(self, u, t):
        return self.manager(u) and (u['role']=='owner' or (t['region']==u['region'] and (u['role']=='region' or t['district']==u['district'])))
    def handle(self, message):
        if message.get('chat',{}).get('type')!='private': return
        uid = message['from']['id']; text = message.get('text','').strip(); u = self.user(uid)
        command = text.split()[0].split('@')[0] if text else ''
        if command == '/id': self.send(uid, f'Telegram ID: {uid}'); return
        if text in ('❌ Bekor qilish','/cancel'):
            self.clear(uid); self.send(uid,'Bekor qilindi.'); return
        if command == '/start':
            self.clear(uid)
            if u:
                self.send(uid, 'Koordinatorlar platformasiga xush kelibsiz!' if u['approved'] else 'Arizangiz tasdiqlanishini kuting.'); return
            self.session(uid,'register',[]); self.send(uid,'Ism va familiyangizni yozing:'); return
        s = self.one('SELECT * FROM sessions WHERE id=?',(uid,))
        if s and s['kind']=='register':
            data=json.loads(s['data']); n=len(data)
            if not text or len(text)>120: self.send(uid,'1–120 belgidan iborat matn kiriting.'); return
            if n==1 and text not in REGIONS: self.send(uid,'Viloyatni tugmadan tanlang.', [[r] for r in REGIONS]); return
            if n==5 and text not in self.directions: self.send(uid,'Yo‘nalishni tugmadan tanlang.',[[d] for d in self.directions]); return
            data.append(text)
            if len(data)==6:
                self.write('INSERT OR IGNORE INTO users(id,name,region,district,school,direction) VALUES(?,?,?,?,?,?)',(uid,data[0],data[1],data[2],data[3],data[5]))
                self.clear(uid); self.send(uid,'Ariza yuborildi. Mas’ul tasdiqlashini kuting.')
                self.notify(self.owner,f'Yangi ariza: {uid}, {data[0]}, {data[1]}, {data[2]}. /pending orqali ko‘ring.'); return
            prompts=['','Viloyatingizni tanlang:','Tuman/shahar nomini yozing (masalan: Qo‘shtepa):','Maktab raqami yoki nomi:','Ma’lumotlar to‘g‘ri bo‘lsa “Ha” yozing:','Yo‘nalishingizni tanlang:']
            self.session(uid,'register',data)
            self.send(uid,prompts[len(data)],[[r] for r in REGIONS] if len(data)==1 else [[d] for d in self.directions] if len(data)==5 else None); return
        if not u: self.send(uid,'Avval /start bosing.'); return
        if not u['approved']: self.send(uid,'Arizangiz hali tasdiqlanmagan.'); return
        try:
            self.authorized(uid,u,text,command,message,s)
        except (ValueError, IndexError):
            self.send(uid,'Format noto‘g‘ri. /help orqali namunani ko‘ring yoki /cancel bosing.')
    def authorized(self, uid,u,text,command,m,s):
        if command=='/help':
            self.send(uid,'/start — menyu\n/id — ID\n/cancel — bekor qilish\n/report TOPSHIRIQ_ID — hisobot\n\nMas’ullar:\n/pending — arizalar\n/approve ID — tasdiqlash\n/newtask — topshiriq\n/reports — hisobotlar\n/accept TOPSHIRIQ_ID USER_ID\n/return TOPSHIRIQ_ID USER_ID Sabab\n/stats — statistika\n\nFaqat bosh administrator:\n/role ID region yoki district yoki member\n/block ID — kirishni yopish\n/suggestions — takliflar'); return
        if command in ('/approve','/block','/role'):
            if not self.manager(u): self.send(uid,'Vakolat yetarli emas.'); return
            parts=text.split(); target=self.user(int(parts[1]))
            if not target or target['id']==self.owner or not self.manages(u,target): self.send(uid,'Bu foydalanuvchi uchun vakolat yo‘q.'); return
            if command=='/approve':
                self.write('UPDATE users SET approved=1 WHERE id=?',(target['id'],)); self.notify(target['id'],'Arizangiz tasdiqlandi. /start bosing.')
            elif u['role']!='owner': self.send(uid,'Faqat bosh administrator.'); return
            elif command=='/block': self.write('UPDATE users SET approved=0 WHERE id=?',(target['id'],))
            else:
                role=parts[2]
                if role not in ('member','region','district'): raise ValueError()
                self.write('UPDATE users SET role=?,approved=1 WHERE id=?',(role,target['id']))
            self.send(uid,'Saqlandi.'); return
        if command=='/pending':
            if not self.manager(u): self.send(uid,'Vakolat yetarli emas.'); return
            items=[f"{x['id']} | {x['name']} | {x['region']} / {x['district']} | {x['school']} | {x['direction']}" for x in self.rows('SELECT * FROM users WHERE approved=0') if self.manages(u,x)]
            self.send(uid,'\n'.join(items) or 'Arizalar yo‘q.'); return
        if command=='/newtask':
            if not self.manager(u): self.send(uid,'Vakolat yetarli emas.'); return
            self.session(uid,'task',{})
            self.send(uid,'Bir xabarda quyidagicha yuboring:\nSarlavha | 2026-12-01 18:00 | Viloyat | Tuman | Yo‘nalish\n\nBarchasi uchun * yozing. Hududiy mas’ul o‘z hududiga topshiriq beradi. Viloyat va yo‘nalish nomi profildagi bilan bir xil bo‘lsin. Vaqt: Toshkent.'); return
        if s and s['kind']=='task':
            if not self.manager(u): self.clear(uid); return
            title,due,region,district,direction=[p.strip() for p in text.split('|')]
            date=datetime.strptime(due,'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
            if date<=datetime.now(TZ) or not title or len(title)>1000: raise ValueError()
            region='' if region=='*' else region; district='' if district=='*' else district; direction='' if direction=='*' else direction
            if region and region not in REGIONS or direction and direction not in self.directions or district and not region: raise ValueError()
            if u['role']!='owner' and (region!=u['region'] or u['role']=='district' and district!=u['district']): self.send(uid,'Faqat o‘z hududingizni kiriting.'); return
            tid=self.write('INSERT INTO tasks(creator,title,due,region,district,direction) VALUES(?,?,?,?,?,?)',(uid,title,due,region,district,direction))
            self.clear(uid); self.send(uid,f'Topshiriq #{tid} saqlandi. Tegishli koordinatorlar “Topshiriqlar” bo‘limida ko‘radi.'); return
        if text=='📋 Topshiriqlar':
            items=[]
            for t in self.rows('SELECT * FROM tasks ORDER BY id DESC'):
                if self.visible(u,t):
                    r=self.one('SELECT status,note FROM reports WHERE task=? AND uid=?',(t['id'],uid))
                    items.append(f"#{t['id']} — {t['title']}\nMuddat: {t['due']}\nHolat: {r['status'] if r else 'Yuborilmagan'} {r['note'] if r else ''}\n/report {t['id']}")
            self.send(uid,'\n\n'.join(items) or 'Topshiriqlar yo‘q.'); return
        if text=='📤 Hisobot yuborish': self.send(uid,'Topshiriq raqamini yozing: /report 1'); return
        if command=='/report':
            tid=int(text.split()[1]); task=self.one('SELECT * FROM tasks WHERE id=?',(tid,))
            if not task or not self.visible(u,task): self.send(uid,'Topshiriq topilmadi.'); return
            r=self.one('SELECT status FROM reports WHERE task=? AND uid=?',(tid,uid))
            if r and r['status']!='Tuzatish kerak': self.send(uid,'Hisobot yuborilgan. Tuzatish so‘ralsa qayta yuborasiz.'); return
            self.session(uid,'report',{'task':tid}); self.send(uid,'Hisobot matnini yoki izohli bitta rasm/hujjat yuboring. Albom yubormang.'); return
        if s and s['kind']=='report':
            tid=json.loads(s['data'])['task']; task=self.one('SELECT * FROM tasks WHERE id=?',(tid,))
            if not task or not self.visible(u,task): self.clear(uid); return
            file=''; kind=''; body=text or m.get('caption','')
            if m.get('photo'): file=m['photo'][-1]['file_id']; kind='photo'
            elif m.get('document'): file=m['document']['file_id']; kind='document'
            if not body or len(body)>3500: self.send(uid,'1–3500 belgidan iborat izoh qo‘shing.'); return
            self.write("INSERT INTO reports(task,uid,body,file,kind) VALUES(?,?,?,?,?) ON CONFLICT(task,uid) DO UPDATE SET body=excluded.body,file=excluded.file,kind=excluded.kind,status='Kutilmoqda',note=''",(tid,uid,body,file,kind))
            self.clear(uid); self.send(uid,'Hisobot yuborildi.'); self.notify(task['creator'],f'#{tid}: yangi hisobot. /reports'); return
        if command in ('/reports','/accept','/return','/stats'):
            if not self.manager(u): self.send(uid,'Vakolat yetarli emas.'); return
            if command in ('/accept','/return'):
                parts=text.split(maxsplit=3); tid,who=int(parts[1]),int(parts[2]); t=self.one('SELECT * FROM tasks WHERE id=?',(tid,))
                if not t or not self.reviewable(u,t): self.send(uid,'Vakolat yetarli emas.'); return
                note=parts[3] if command=='/return' else ''
                if command=='/return' and not note: raise ValueError()
                if not self.one('SELECT * FROM reports WHERE task=? AND uid=?',(tid,who)): self.send(uid,'Hisobot topilmadi.'); return
                status='Qabul qilindi' if command=='/accept' else 'Tuzatish kerak'
                self.write('UPDATE reports SET status=?,note=? WHERE task=? AND uid=?',(status,note,tid,who)); self.send(uid,'Saqlandi.'); self.notify(who,f'#{tid}: {status}. {note}'); return
            count=0
            for t in self.rows('SELECT * FROM tasks ORDER BY id DESC'):
                if not self.reviewable(u,t): continue
                reports=self.rows('SELECT * FROM reports WHERE task=?',(t['id'],)); count+=1
                if command=='/stats':
                    members=[x for x in self.rows("SELECT * FROM users WHERE approved=1 AND role='member'") if self.visible(x,t)]
                    sent={r['uid'] for r in reports}; missing=[x['name'] for x in members if x['id'] not in sent]
                    self.send(uid,f"#{t['id']} {t['title']}\nHisobotlar: {len(reports)}; qabul: {sum(r['status']=='Qabul qilindi' for r in reports)}\nYubormaganlar: {', '.join(missing) or 'yo‘q'}")
                else:
                    for r in reports:
                        self.send(uid,f"#{t['id']} | foydalanuvchi {r['uid']} | {r['status']}\n{r['body']}\n/accept {t['id']} {r['uid']}\n/return {t['id']} {r['uid']} Sabab")
                        if r['file']: self.api.call('sendPhoto' if r['kind']=='photo' else 'sendDocument',chat_id=uid,**{r['kind']:r['file']})
            self.send(uid,'Ko‘rish yakunlandi.' if count else 'Topshiriqlar yo‘q.'); return
        if text=='👤 Profil': self.send(uid,'\n'.join(f'{k}: {u[k]}' for k in ('id','name','region','district','school','direction','role'))); return
        if text=='⚙️ Boshqaruv': self.send(uid,'/pending\n/newtask\n/reports\n/stats\n/help' if self.manager(u) else 'Bu bo‘lim mas’ullar uchun.'); return
        if text=='💡 Taklif': self.session(uid,'suggestion',{}); self.send(uid,'Taklifingizni yozing:'); return
        if s and s['kind']=='suggestion':
            if not text or len(text)>3500: raise ValueError()
            self.write('INSERT INTO suggestions(uid,body) VALUES(?,?)',(uid,text)); self.clear(uid); self.send(uid,'Taklif saqlandi.'); return
        if command=='/suggestions' and u['role']=='owner':
            self.send(uid,'\n\n'.join(f"{r['uid']}: {r['body']}" for r in self.rows('SELECT * FROM suggestions ORDER BY id DESC LIMIT 50')) or 'Taklif yo‘q.'); return
        self.send(uid,'Menyudan tanlang yoki /help bosing.')
    def remind(self):
        now=datetime.now(TZ)
        for t in self.rows('SELECT * FROM tasks'):
            delta=datetime.strptime(t['due'],'%Y-%m-%d %H:%M').replace(tzinfo=TZ)-now
            if not timedelta(0)<delta<=timedelta(days=1): continue
            for u in self.rows('SELECT * FROM users WHERE approved=1'):
                if not self.visible(u,t) or self.one('SELECT 1 FROM reminders WHERE task=? AND uid=?',(t['id'],u['id'])): continue
                r=self.one('SELECT status FROM reports WHERE task=? AND uid=?',(t['id'],u['id']))
                if r and r['status']!='Tuzatish kerak': continue
                try: self.send(u['id'],f"Eslatma: #{t['id']} {t['title']}\nMuddat: {t['due']}")
                except RuntimeError: continue
                self.write('INSERT OR IGNORE INTO reminders VALUES(?,?)',(t['id'],u['id']))

def run_polling(token, owner, db_path, directions, stop, state):
    # SQLite ulanishi shu worker thread ichida yaratiladi va ishlatiladi.
    app = App(db_path, Telegram(token), owner, directions)
    try:
        me = app.api.call('getMe')
        hook = app.api.call('getWebhookInfo')
        if hook.get('url'):
            raise RuntimeError('Webhook yoqilgan. Polling uchun avval webhookni o‘chiring.')
        state['ready'] = True
        print('Bot ishga tushdi: @' + me['username'], flush=True)
        offset = int((app.one("SELECT value FROM meta WHERE key='offset'") or {'value':'0'})['value'])
        last = 0
        while not stop.is_set():
            try:
                updates = app.api.call('getUpdates', offset=offset, timeout=25, allowed_updates=['message'])
                state['last_poll'] = time.monotonic()
                for update in updates:
                    if stop.is_set():
                        break
                    if 'message' in update:
                        app.handle(update['message'])
                    offset = update['update_id'] + 1
                    app.write("INSERT OR REPLACE INTO meta VALUES('offset',?)", (str(offset),))
                if not stop.is_set() and time.monotonic() - last > 60:
                    app.remind()
                    last = time.monotonic()
            except RuntimeError as exc:
                # Xatolar token yoki to‘liq so‘rov URL ini logga chiqarmaydi.
                print(str(exc), flush=True)
                stop.wait(5)
    finally:
        state['ready'] = False
        app.db.close()


def create_web_app(state):
    async def ping(request):
        return web.json_response({'service': 'Koordinatorlar platformasi', 'status': 'ok'})

    async def health(request):
        healthy = state.get('ready', False) and time.monotonic() - state.get('last_poll', 0) < 180
        return web.json_response({'status': 'ok' if healthy else 'starting_or_unavailable'}, status=200 if healthy else 503)

    app = web.Application()
    app.router.add_get('/', ping)  # UptimeRobot uchun; GET va HEAD ishlaydi.
    app.router.add_get('/health', health)  # Telegram polling holati ham tekshiriladi.
    return app


async def serve(token, owner, port, db_path, directions):
    stop = threading.Event()
    shutdown = asyncio.Event()
    state = {'ready': False, 'last_poll': 0.0}
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: asyncio.run KeyboardInterrupt ni boshqaradi.
    runner = web.AppRunner(create_web_app(state), access_log=None)
    await runner.setup()
    worker = waiter = None
    try:
        await web.TCPSite(runner, host='0.0.0.0', port=port).start()
        print(f'HTTP server: 0.0.0.0:{port}', flush=True)
        worker = asyncio.create_task(asyncio.to_thread(run_polling, token, owner, db_path, directions, stop, state))
        waiter = asyncio.create_task(shutdown.wait())
        done, _ = await asyncio.wait({worker, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker  # Bot yiqilsa, HTTP servergina ishlab qolmaydi.
    finally:
        stop.set()
        if waiter:
            waiter.cancel()
        await runner.cleanup()
        if worker:
            await asyncio.shield(worker)


def main():
    load_env()  # Render env ustun; .env faqat lokal ishlatish uchun.
    token = os.environ.get('BOT_TOKEN', '').strip()
    owner = os.environ.get('OWNER_ID', '').strip()
    if not token or not owner.isdigit() or int(owner) <= 0:
        raise SystemExit('BOT_TOKEN va musbat OWNER_ID environment variables ni kiriting.')
    try:
        port = int(os.environ.get('PORT', '10000'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise SystemExit('PORT 1–65535 oralig‘idagi son bo‘lsin.') from None
    db_path = Path(os.environ.get('DATABASE_PATH', str(ROOT / 'data.sqlite3')))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    try:
        asyncio.run(serve(token, int(owner), port, db_path, config['directions']))
    except KeyboardInterrupt:
        print('Bot to‘xtatildi.', flush=True)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == '__main__':
    main()
