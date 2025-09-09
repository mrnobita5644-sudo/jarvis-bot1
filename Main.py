import discord
from discord.ext import commands, tasks
import datetime
import random
import wikipedia
import webbrowser
import requests
import json
import os
import re
from collections import defaultdict, deque
from difflib import get_close_matches

# ---------------- CONFIG ----------------
TOKEN = "MTQxNDYyNzczODc1MjY1MTM0NA.GtbeXU.txlGbSSZu-K8MDJiL-2fYD3yPSwbpD0N8DKnxE"
OWNER_ID = 1390567860153221151   
COMMAND_PREFIX = "!"            # admin/prefix commands (owner may use non-prefix)
WHITELIST_ROLES = []            # optional trusted role IDs (ints)
MEMFILE = "jarvis_memory.json"  # persistent memory
AUTO_MOD_BAN_WORDS = ["badword1", "badword2"]  # customize
INVITE_REGEX = re.compile(r"(?:https?:\/\/)?(?:www\.)?(?:discord\.gg|discordapp\.com\/invite)\/\S+", re.I)
SPAM_THRESHOLD = 6
SPAM_WINDOW = 7  # seconds
NUKE_ACTION_THRESHOLD = 3
NUKE_WINDOW = 20
# ----------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# in-memory trackers
user_msg_times = defaultdict(lambda: deque())
recent_destructions = defaultdict(lambda: deque())  # guild_id -> deque of (timestamp, action, executor_id)
trusted_whitelist = set()  # dynamic id whitelist if needed

# ensure memory file exists
if not os.path.exists(MEMFILE):
    with open(MEMFILE, "w") as f:
        json.dump({}, f)

def load_memory():
    with open(MEMFILE, "r") as f:
        return json.load(f)

def save_memory(mem):
    with open(MEMFILE, "w") as f:
        json.dump(mem, f, indent=2)

# ---------------- Helpers ----------------
def is_owner(user):
    return user and user.id == OWNER_ID

def is_trusted(member: discord.Member):
    if not member:
        return False
    if is_owner(member):
        return True
    if any(r.id in WHITELIST_ROLES for r in member.roles):
        return True
    if member.id in trusted_whitelist:
        return True
    return False

async def punish_executor(guild: discord.Guild, executor_id: int, reason="Anti-nuke"):
    try:
        member = guild.get_member(executor_id)
        if not member:
            return False
        if is_trusted(member):
            return False
        # try remove dangerous perms by removing roles (best-effort)
        try:
            for r in list(member.roles)[1:]:
                await member.remove_roles(r, reason=reason)
        except Exception:
            pass
        # then kick or ban
        try:
            await member.kick(reason=reason)
        except Exception:
            try:
                await guild.ban(member, reason=reason)
            except Exception:
                pass
        return True
    except Exception:
        return False

# ------------- smart helpers ("thinking") -------------
# A small intent matcher based on keywords and fuzzy matching
INTENT_KEYWORDS = {
    "time": ["time", "hour", "kya time"],
    "date": ["date", "aaj ki date"],
    "joke": ["joke", "mazak", "hasao"],
    "search": ["search", "find", "kho"],
    "google": ["google", "open google"],
    "youtube": ["youtube", "open youtube"],
    "remember": ["yaad kar", "remember"],
    "recall": ["yaad kya hai", "recall"],
    "advice": ["advice", "kya karun"],
    "calculate": ["calculate", "what is", "solve"]
}

def guess_intent(text):
    text = text.lower()
    # direct keyword check
    for intent, kws in INTENT_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                return intent
    # fuzzy check
    tokens = re.findall(r"[a-zA-Z0-9]+", text)
    for t in tokens:
        matches = get_close_matches(t, sum(INTENT_KEYWORDS.values(), []), n=1, cutoff=0.8)
        if matches:
            # find which intent contains this match
            for intent, kws in INTENT_KEYWORDS.items():
                if matches[0] in kws:
                    return intent
    return None

def safe_eval(expr: str):
    # VERY limited eval - only math expressions with digits/operators
    if re.fullmatch(r"[\d\s\.\+\-\*\/\(\)]+", expr):
        try:
            return eval(expr, {"__builtins__": {}}, {})
        except Exception:
            return None
    return None

def duckduckgo_instant(query: str):
    # DuckDuckGo Instant Answer API (no API key)
    try:
        q = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        r = requests.get("https://api.duckduckgo.com/", params=q, timeout=6)
        if r.status_code == 200:
            data = r.json()
            # prefer AbstractText, else RelatedTopics
            if data.get("AbstractText"):
                return data.get("AbstractText")
            topics = data.get("RelatedTopics") or []
            if topics:
                # try first topic text
                first = topics[0]
                if isinstance(first, dict) and first.get("Text"):
                    return first.get("Text")
        return None
    except Exception:
        return None

def assistant_think_and_respond(text: str, guild=None):
    """
    Main "thinking" function. Returns a string response or a special command token
    like "OPEN_GOOGLE" to be handled by host.
    """
    text = (text or "").strip()
    if not text:
        return "Haan boss?"

    # check memory commands first (yaad)
    if text.lower().startswith("yaad kar"):
        parts = text.split(None, 2)
        if len(parts) >= 3:
            key = parts[1].strip()
            value = parts[2].strip()
            mem = load_memory()
            mem[key] = value
            save_memory(mem)
            return f"Yaad rakh liya boss: {key} = {value}"
        else:
            return "Format: yaad kar <key> <value>"

    if text.lower().startswith("yaad kya hai"):
        key = text[len("yaad kya hai"):].strip()
        mem = load_memory()
        if key in mem:
            return mem[key]
        return "Mujhe wo yaad nahi hai boss."

    # guess intent
    intent = guess_intent(text)

    # calculator
    if intent == "calculate" or text.lower().startswith("calculate"):
        expr = text
        expr = expr.lower().replace("calculate", "").strip()
        val = safe_eval(expr)
        if val is not None:
            return f"Boss, iska answer hai {val}"
        return "Calculation samajh nahi aaya boss."

    # time/date
    if intent == "time":
        now = datetime.datetime.now().strftime("%H:%M")
        return f"Boss, abhi ka time hai {now}"
    if intent == "date":
        today = datetime.date.today().strftime("%d-%m-%Y")
        return f"Boss, aaj ki date hai {today}"

    # jokes
    if intent == "joke":
        jokes = [
            "Computer ka favorite snack: micro-chips! 😂",
            "Ek programmer bola: 'Mujhe coffee se zyada errors milte hai' ☕🐞"
        ]
        return random.choice(jokes)

    # simple open commands
    if intent == "google" or "google" in text.lower():
        return "OPEN_GOOGLE"
    if intent == "youtube" or "youtube" in text.lower():
        return "OPEN_YOUTUBE"

    # search/wikipedia/duckduckgo
    if intent == "search" or text.lower().startswith("search"):
        topic = text
        topic = re.sub(r"^(search|search for)", "", topic, flags=re.I).strip()
        if not topic:
            return "Boss, kya search karun?"
        # try DuckDuckGo instant
        dd = duckduckgo_instant(topic)
        if dd:
            return dd
        # fallback to wikipedia
        try:
            s = wikipedia.summary(topic, sentences=2, auto_suggest=False)
            return s
        except Exception:
            return "Kuch zyada nahi mila, boss."

    # advice / mini-ai
    if intent == "advice":
        adv = [
            "Mera suggestion: chhote step se start karo.",
            "If unsure, test on small scale first."
        ]
        return random.choice(adv)

    # fallback: try DuckDuckGo then wikipedia then generic reply
    dd = duckduckgo_instant(text)
    if dd:
        return dd
    try:
        wiki = wikipedia.summary(text, sentences=2, auto_suggest=False)
        return wiki
    except Exception:
        # generic friendly reply with suggestion
        return "Boss, mujhe thoda clearly batao kya karna hai — ya 'search <topic>' try karo."

# ---------------- AutoMod & Anti-spam ----------------
async def automod_check(message: discord.Message):
    if message.author.bot or is_owner(message.author):
        return
    content = (message.content or "")
    lowered = content.lower()

    # invite links
    if INVITE_REGEX.search(content):
        try:
            await message.delete()
            await message.channel.send(f"{message.author.mention} Invite links are not allowed.")
        except Exception:
            pass
        return

    # banned words
    for bad in AUTO_MOD_BAN_WORDS:
        if bad in lowered:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} That language isn't allowed.")
            except Exception:
                pass
            return

    # caps detection
    letters = sum(1 for c in content if c.isalpha())
    if letters >= 8:
        caps = sum(1 for c in content if c.isupper())
        if caps / max(letters,1) > 0.7:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Please avoid excessive CAPS.")
            except Exception:
                pass
            return

    # spam detection
    now = datetime.datetime.utcnow().timestamp()
    dq = user_msg_times[message.author.id]
    dq.append(now)
    while dq and now - dq[0] > SPAM_WINDOW:
        dq.popleft()
    if len(dq) >= SPAM_THRESHOLD:
        try:
            await message.channel.send(f"{message.author.mention} Stop spamming.")
        except Exception:
            pass

# ------------- Anti-nuke event recording -------------
async def record_destruction(guild_id, action, executor_id):
    q = recent_destructions[guild_id]
    now = datetime.datetime.utcnow().timestamp()
    q.append((now, action, executor_id))
    while q and now - q[0][0] > NUKE_WINDOW:
        q.popleft()
    # count per executor
    counts = {}
    for ts, act, exec_id in q:
        counts[exec_id] = counts.get(exec_id, 0) + 1
    for exec_id, cnt in counts.items():
        if cnt >= NUKE_ACTION_THRESHOLD:
            guild = bot.get_guild(guild_id)
            if guild:
                ok = await punish_executor(guild, exec_id, reason="Detected mass destructive actions")
                # notify owner
                try:
                    owner = await bot.fetch_user(OWNER_ID)
                    if owner:
                        await owner.send(f"Anti-nuke: action taken in {guild.name} against <@{exec_id}>; punished={ok}")
                except Exception:
                    pass
            recent_destructions[guild_id].clear()
            break

# Hook destructive events (channel/role create/delete, bans)
@bot.event
async def on_guild_channel_create(channel):
    try:
        guild = channel.guild
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
            await record_destruction(guild.id, "channel_create", entry.user.id)
    except Exception:
        pass

@bot.event
async def on_guild_channel_delete(channel):
    try:
        guild = channel.guild
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            await record_destruction(guild.id, "channel_delete", entry.user.id)
    except Exception:
        pass

@bot.event
async def on_guild_role_create(role):
    try:
        guild = role.guild
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_create):
            await record_destruction(guild.id, "role_create", entry.user.id)
    except Exception:
        pass

@bot.event
async def on_guild_role_delete(role):
    try:
        guild = role.guild
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
            await record_destruction(guild.id, "role_delete", entry.user.id)
    except Exception:
        pass

@bot.event
async def on_member_ban(guild, user):
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
            await record_destruction(guild.id, "ban", entry.user.id)
    except Exception:
        pass

# ----------------- Message handler -----------------
@bot.event
async def on_message(message: discord.Message):
    # ignore self and other bots
    if message.author.bot:
        return

    # run automod
    await automod_check(message)

    content_strip = (message.content or "").strip()

    # OWNER ONLY: if owner types exact "Jarvis" -> acknowledgement only
    if is_owner(message.author) and content_strip.lower() == "jarvis":
        try:
            await message.channel.send("Yes boss")
        except Exception:
            pass
        return

    # OWNER ONLY: owner may issue commands without prefix anywhere the bot can read
    if is_owner(message.author):
        # Accept: "Jarvis <command>" or direct "<command>"
        text = content_strip
        if text.lower().startswith("jarvis"):
            cmd_text = text[len("jarvis"):].strip()
        else:
            cmd_text = text

        if cmd_text:
            # acknowledge and process
            try:
                await message.channel.send("Yes boss")
            except Exception:
                pass

            # prevent any illegal or dangerous request: explicit blocklist
            lower = cmd_text.lower()
            illegal_terms = ["hack", "ddos", "doxx", "dox", "steal", "password", "credit card", "card number"]
            if any(t in lower for t in illegal_terms):
                await message.channel.send("Boss, main illegal kaam nahi kar sakta. Koi aur hukm de.")
                return

            # process via thinking function
            result = assistant_think_and_respond(cmd_text, guild=message.guild)
            # result may be special tokens
            if result == "OPEN_GOOGLE":
                # tell owner to open locally (host may or may not open)
                await message.channel.send("Opening Google on host (if supported).")
                # optional: if running on machine with GUI, you could webbrowser.open("https://google.com")
            elif result == "OPEN_YOUTUBE":
                await message.channel.send("Opening YouTube on host (if supported).")
            else:
                # long results may be big - chunk if needed
                if isinstance(result, str) and len(result) > 1900:
                    # split into chunks
                    for i in range(0, len(result), 1900):
                        await message.channel.send(result[i:i+1900])
                else:
                    await message.channel.send(result)
            return

    # For non-owner messages: only process prefix commands normally
    await bot.process_commands(message)

# ----------------- Some prefix commands for admins -----------------
@bot.command()
@commands.has_permissions(administrator=True)
async def vault(ctx, *, key: str = None):
    """Simple view/update memory for admins (prefixed command)"""
    mem = load_memory()
    if not key:
        await ctx.send("Usage: !vault <key>  OR !vault set <key> <value>")
        return
    parts = key.split(" ", 2)
    if parts[0].lower() == "set" and len(parts) >= 3:
        k = parts[1]
        v = parts[2]
        mem[k] = v
        save_memory(mem)
        await ctx.send(f"Saved {k}.")
    else:
        if key in mem:
            await ctx.send(f"{key} = {mem[key]}")
        else:
            await ctx.send("No such key.")

@bot.command()
@commands.has_permissions(administrator=True)
async def info(ctx):
    await ctx.send("Jarvis advanced bot. Owner-only assistant + automod + anti-nuke.")

# -------------- Run --------------
if __name__ == "__main__":
    print("Starting Jarvis advanced bot...")
    bot.run(TOKEN)
