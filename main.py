import os
import random
import json
import time
import datetime
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
import aiohttp

# ─────────────────────────────────────────────
# 1. CONFIGURATION
# ─────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=[".", "+"], intents=intents, help_command=None)

# ─────────────────────────────────────────────
# 2. DATABASE
# ─────────────────────────────────────────────
DB_FILE = "users.json"

def get_db():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def ensure_user(data, user_id):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    defaults = {
        "wallet": 0, "bank": 0, "credits": 0,
        "xp": 0, "level": 1, "prestige": 0,
        "messages": {}, "charms": 0,
        "booster_end": 0,
        "lootcrates": 0,
        "blacktea_wins": 0,
        "last_daily": 0, "last_weekly": 0, "last_work": 0, "last_rob": 0,
        "partner": None, "marry_date": 0,
        "inbox": [],
        "afk": None,
    }
    for k, v in defaults.items():
        if k not in data[uid]:
            data[uid][k] = v
    return data

def parse_amount(s, balance):
    s = str(s).lower().strip()
    if s == "all":  return balance
    if s == "half": return balance // 2
    for suffix, mult in [("b", 1_000_000_000), ("m", 1_000_000), ("k", 1_000)]:
        if s.endswith(suffix):
            try: return int(float(s[:-1]) * mult)
            except: return None
    try: return int(s)
    except: return None

def get_multiplier(data, user_id):
    return 2 if data[str(user_id)].get("booster_end", 0) > time.time() else 1

def add_xp(data, user_id, amount):
    uid = str(user_id)
    data[uid]["xp"] += amount
    needed = (data[uid]["level"] + data[uid].get("prestige", 0) * 10) * 500
    if data[uid]["xp"] >= needed:
        data[uid]["level"] += 1
        data[uid]["xp"] = 0
        return True
    return False

# ─────────────────────────────────────────────
# 3. CACHES
# ─────────────────────────────────────────────
snipe_cache  = {}  # channel_id -> dict
afk_cache    = {}  # user_id    -> reason
msg_cooldown = {}  # user_id    -> timestamp

# ─────────────────────────────────────────────
# 4. ANIME GIF HELPER
# ─────────────────────────────────────────────
async def fetch_gif(action: str) -> str:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://nekos.best/api/v2/{action}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["results"][0]["url"]
    except Exception:
        pass
    return ""

async def send_action(ctx, action: str, member: discord.Member, color: discord.Color):
    phrases = {
        "hug":      f"💞 **{ctx.author.display_name}** hugs **{member.display_name}**!",
        "kiss":     f"💋 **{ctx.author.display_name}** kisses **{member.display_name}**!",
        "dance":    f"💃 **{ctx.author.display_name}** dances with **{member.display_name}**!",
        "handhold": f"🤝 **{ctx.author.display_name}** holds hands with **{member.display_name}**!",
        "cry":      f"😢 **{ctx.author.display_name}** cries with **{member.display_name}**!",
        "bite":     f"😬 **{ctx.author.display_name}** bites **{member.display_name}**!",
        "poke":     f"👉 **{ctx.author.display_name}** pokes **{member.display_name}**!",
        "lick":     f"👅 **{ctx.author.display_name}** licks **{member.display_name}**!",
        "highfive": f"🙌 **{ctx.author.display_name}** high-fives **{member.display_name}**!",
        "slap":     f"👋 **{ctx.author.display_name}** slaps **{member.display_name}**!",
        "cuddle":   f"🫂 **{ctx.author.display_name}** cuddles **{member.display_name}**!",
        "kill":     f"⚔️ **{ctx.author.display_name}** kills **{member.display_name}**!",
    }
    gif = await fetch_gif(action)
    embed = discord.Embed(description=phrases.get(action, f"**{ctx.author.display_name}** → **{member.display_name}**"), color=color)
    if gif:
        embed.set_image(url=gif)
    else:
        embed.set_footer(text="(GIF unavailable)")
    await ctx.send(embed=embed)

# ─────────────────────────────────────────────
# 5. HELP MENU
# ─────────────────────────────────────────────
class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    def main_embed(self):
        embed = discord.Embed(
            title="📖  Help Menu",
            description=(
                "Use the buttons below to explore each category.\n\n"
                "**Categories**\n"
                "🔹 **General** — Profile, rank, marriage, leaderboards & more\n"
                "💰 **Economy** — Earn, gamble, rob, duel & manage money\n"
                "🎭 **Fun** — Anime roleplay actions & social games\n\n"
                "`< >` required  •  `[ ]` optional"
            ),
            color=0x5865F2
        )
        embed.set_footer(text="Select a category below")
        return embed

    def general_embed(self):
        cmds = [
            ("`.credits`",                           "View your credits"),
            ("`.charms [member]`",                   "View charm count"),
            ("`.leaderboard [category] [page]`",     "View leaderboards (economy / leveling / charm / lootcrate / blacktea)"),
            ("`.boosters`",                          "View active boosters"),
            ("`.rank [member]`",                     "View a member's rank & XP"),
            ("`.prestige`",                          "Prestige at Level 50 to reset & gain prestige"),
            ("`.blacktea`",                          "Start a word-unscramble Blacktea game"),
            ("`+charm <member>`",                    "Give a charm to someone"),
            ("`.messages [member]`",                 "Check message count per channel"),
            ("`.snipe`",                             "View last deleted/edited message"),
            ("`.afk [reason]`",                      "Set yourself as AFK"),
            ("`.partner [member]`",                  "View marriage details"),
            ("`.marry <member>`",                    "Propose marriage"),
            ("`.divorce`",                           "Divorce your spouse"),
        ]
        embed = discord.Embed(title="🔹 General Commands", color=0x3498DB)
        embed.description = "\n".join(f"**{c}** — {d}" for c, d in cmds)
        return embed

    def economy_embed(self):
        cmds = [
            ("`.balance [member]`",          "Check balance"),
            ("`.deposit <amount>`",          "Wallet → Bank"),
            ("`.withdraw <amount>`",         "Bank → Wallet"),
            ("`.give <member> <amount>`",    "Give money to someone"),
            ("`.work`",                      "Work for money (36s cooldown)"),
            ("`.daily`",                     "Daily reward (24h cooldown)"),
            ("`.weekly`",                    "Weekly reward (7d cooldown)"),
            ("`.rob <member>`",              "Rob someone's wallet"),
            ("`.cooldowns [member]`",        "Check all cooldowns"),
            ("`.inbox [page]`",              "View your inbox"),
            ("`.coinflip <amount> <h|t>`",   "Flip a coin"),
            ("`.blackjack <amount>`",        "Play blackjack"),
            ("`.roulette <amount> <type>`",  "Bet on roulette"),
            ("`.rps <member> <amount>`",     "Rock Paper Scissors"),
            ("`.duel <member> <amount>`",    "50/50 coin duel"),
            ("`.ttt <member> <amount>`",     "Tic-Tac-Toe duel"),
        ]
        embed = discord.Embed(title="💰 Economy Commands", color=0xF1C40F)
        embed.description = "\n".join(f"**{c}** — {d}" for c, d in cmds)
        return embed

    def fun_embed(self):
        cmds = [
            ("`.hug <member>`",              "Hug someone"),
            ("`.kiss <member>`",             "Kiss someone"),
            ("`.dance <member>`",            "Dance with someone"),
            ("`.handhold <member>`",         "Hold hands"),
            ("`.cry <member>`",              "Cry together"),
            ("`.bite <member>`",             "Bite someone"),
            ("`.poke <member>`",             "Poke someone"),
            ("`.lick <member>`",             "Lick someone"),
            ("`.highfive <member>`",         "High-five someone"),
            ("`.slap <member>`",             "Slap someone"),
            ("`.cuddle <member>`",           "Cuddle someone"),
            ("`.kill <member>`",             "Kill someone dramatically"),
            ("`.bestie <member> [member]`",  "Bestie compatibility %"),
            ("`.aura [member]`",             "Check your aura power"),
            ("`.ship <member> [member]`",    "Ship two members"),
        ]
        embed = discord.Embed(title="🎭 Fun Commands", color=0xE91E63)
        embed.description = "\n".join(f"**{c}** — {d}" for c, d in cmds)
        return embed

    @discord.ui.button(label="General",  style=discord.ButtonStyle.primary,   emoji="🔹")
    async def general_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.general_embed(), view=self)

    @discord.ui.button(label="Economy",  style=discord.ButtonStyle.success,   emoji="💰")
    async def economy_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.economy_embed(), view=self)

    @discord.ui.button(label="Fun",      style=discord.ButtonStyle.secondary, emoji="🎭")
    async def fun_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.fun_embed(), view=self)

    @discord.ui.button(label="Back",     style=discord.ButtonStyle.danger,    emoji="↩️")
    async def back_btn(self, interaction, btn):
        await interaction.response.edit_message(embed=self.main_embed(), view=self)

# ─────────────────────────────────────────────
# 6. EVENTS
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online | Prefixes: . and +")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    uid  = str(message.author.id)
    now  = time.time()
    chan = str(message.channel.id)

    # AFK return
    if uid in afk_cache:
        del afk_cache[uid]
        data = get_db(); ensure_user(data, message.author.id)
        data[uid]["afk"] = None; save_db(data)
        try:
            await message.channel.send(f"👋 Welcome back, {message.author.mention}! AFK removed.", delete_after=5)
        except Exception:
            pass

    # Ping AFK check
    for mention in message.mentions:
        mid = str(mention.id)
        if mid in afk_cache:
            reason = afk_cache[mid] or "No reason given"
            try:
                await message.channel.send(f"💤 **{mention.display_name}** is AFK: {reason}", delete_after=8)
            except Exception:
                pass

    # XP & credits (30s cooldown per user)
    if uid not in msg_cooldown or now - msg_cooldown[uid] > 30:
        data = get_db(); ensure_user(data, message.author.id)
        data[uid]["credits"] = data[uid].get("credits", 0) + 5
        data[uid]["messages"][chan] = data[uid]["messages"].get(chan, 0) + 1
        if add_xp(data, message.author.id, 20):
            try:
                await message.channel.send(
                    f"🎊 {message.author.mention} leveled up to **Level {data[uid]['level']}**!"
                )
            except Exception:
                pass
        save_db(data)
        msg_cooldown[uid] = now

    await bot.process_commands(message)

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.content:
        return
    snipe_cache[str(message.channel.id)] = {
        "content": message.content,
        "author":  str(message.author),
        "avatar":  str(message.author.display_avatar.url),
        "time":    time.time(),
        "type":    "deleted",
    }

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return
    snipe_cache[str(before.channel.id)] = {
        "content": before.content,
        "author":  str(before.author),
        "avatar":  str(before.author.display_avatar.url),
        "time":    time.time(),
        "type":    "edited",
    }

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏱️ Cooldown! Try again in **{error.retry_after:.1f}s**.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing: `{error.param.name}`. Use `.help` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument. Use `.help` for usage.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You don't have permission for that.")
    else:
        raise error

# ─────────────────────────────────────────────
# 7. GENERAL COMMANDS
# ─────────────────────────────────────────────
@bot.command()
async def help(ctx):
    view = HelpView()
    await ctx.send(embed=view.main_embed(), view=view)

@bot.command()
async def credits(ctx):
    data = get_db(); ensure_user(data, ctx.author.id)
    u = data[str(ctx.author.id)]
    embed = discord.Embed(title=f"🪙 {ctx.author.display_name}'s Credits", color=0xF1C40F)
    embed.add_field(name="Credits", value=f"**{u['credits']:,}**")
    embed.set_footer(text="Earn credits by chatting every 30s (+5 each time)")
    await ctx.send(embed=embed)

@bot.command()
async def charms(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id)
    embed = discord.Embed(
        title=f"✨ {t.display_name}'s Charms",
        description=f"**{data[str(t.id)]['charms']:,}** charms",
        color=0xFF69B4
    )
    await ctx.send(embed=embed)

@bot.command(name="charm")
async def give_charm(ctx, member: discord.Member):
    """Used with + prefix: +charm <member>"""
    if member == ctx.author:
        return await ctx.send("❌ You can't charm yourself.")
    data = get_db()
    ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    data[str(member.id)]["charms"] += 1
    save_db(data)
    await ctx.send(f"✨ {ctx.author.mention} gave a charm to **{member.display_name}**! They now have **{data[str(member.id)]['charms']}** charms.")

@bot.command(aliases=["lb"])
async def leaderboard(ctx, category: str = "economy", page: int = 1):
    data = get_db()
    page = max(1, page)
    per  = 10
    offset = (page - 1) * per

    categories = {
        "economy":   ("💰 Economy",   lambda x: x[1].get("wallet",0)+x[1].get("bank",0), lambda v: f"${v:,}"),
        "leveling":  ("📊 Leveling",  lambda x: (x[1].get("level",1), x[1].get("xp",0)), lambda v: f"Lvl {v[0]} ({v[1]} XP)"),
        "charm":     ("✨ Charms",    lambda x: x[1].get("charms",0),                     lambda v: f"{v:,} charms"),
        "lootcrate": ("📦 Loot Crates",lambda x: x[1].get("lootcrates",0),               lambda v: f"{v:,} crates"),
        "blacktea":  ("🍵 Blacktea",  lambda x: x[1].get("blacktea_wins",0),              lambda v: f"{v:,} wins"),
    }
    cat = category.lower()
    if cat not in categories:
        return await ctx.send(f"❌ Invalid category. Choose: {', '.join(categories)}")

    title, key_fn, fmt_fn = categories[cat]
    sorted_list = sorted(data.items(), key=key_fn, reverse=True)
    total_pages = max(1, (len(sorted_list) + per - 1) // per)
    page = min(page, total_pages)
    slice_ = sorted_list[offset:offset+per]

    embed = discord.Embed(title=f"{title} Leaderboard", color=0xFFD700)
    medals = {1:"🥇", 2:"🥈", 3:"🥉"}
    lines = []
    for i, (uid, u) in enumerate(slice_, start=offset+1):
        badge = medals.get(i, f"**{i}.**")
        val   = key_fn(("", u))
        lines.append(f"{badge} <@{uid}> — {fmt_fn(val)}")
    embed.description = "\n".join(lines) if lines else "No data yet."
    embed.set_footer(text=f"Page {page}/{total_pages}")
    await ctx.send(embed=embed)

@bot.command()
async def boosters(ctx):
    data = get_db(); ensure_user(data, ctx.author.id)
    u = data[str(ctx.author.id)]
    embed = discord.Embed(title=f"🚀 {ctx.author.display_name}'s Boosters", color=0x9B59B6)
    rem = u.get("booster_end", 0) - time.time()
    if rem > 0:
        embed.add_field(name="2x Booster", value=f"Expires in **{str(datetime.timedelta(seconds=int(rem)))}**")
    else:
        embed.description = "You have no active boosters."
    await ctx.send(embed=embed)

@bot.command()
async def rank(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id)
    u = data[str(t.id)]
    sorted_users = sorted(
        data.items(),
        key=lambda x: (x[1].get("prestige",0), x[1].get("level",1), x[1].get("xp",0)),
        reverse=True
    )
    pos = next((i+1 for i,(uid,_) in enumerate(sorted_users) if uid == str(t.id)), "?")
    needed    = u["level"] * 500
    filled    = int((u["xp"] / needed) * 20) if needed else 0
    bar       = "█" * filled + "░" * (20 - filled)
    embed = discord.Embed(title=f"📊 {t.display_name}'s Rank", color=0x3498DB)
    embed.set_thumbnail(url=t.display_avatar.url)
    embed.add_field(name="Rank",     value=f"**#{pos}**",                inline=True)
    embed.add_field(name="Level",    value=f"**{u['level']}**",          inline=True)
    embed.add_field(name="Prestige", value=f"**{u.get('prestige',0)}**", inline=True)
    embed.add_field(name="XP",       value=f"`{bar}` {u['xp']}/{needed}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def prestige(ctx):
    data = get_db(); ensure_user(data, ctx.author.id)
    uid = str(ctx.author.id); u = data[uid]
    if u["level"] < 50:
        return await ctx.send(f"❌ Reach **Level 50** to prestige. You are Level {u['level']}.")
    u["prestige"] = u.get("prestige",0) + 1
    u["level"] = 1; u["xp"] = 0
    save_db(data)
    await ctx.send(f"🌟 {ctx.author.mention} prestiged! Now **Prestige {u['prestige']}** — Level reset to 1.")

@bot.command()
async def messages(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id)
    msg_data = data[str(t.id)].get("messages", {})
    if not msg_data:
        return await ctx.send(f"📭 No message data for **{t.display_name}** yet.")
    embed = discord.Embed(title=f"💬 {t.display_name}'s Messages", color=0x2ECC71)
    total = sum(msg_data.values())
    top   = sorted(msg_data.items(), key=lambda x: x[1], reverse=True)[:10]
    embed.description = "\n".join(f"<#{cid}> — **{cnt:,}**" for cid, cnt in top)
    embed.set_footer(text=f"Total: {total:,} messages")
    await ctx.send(embed=embed)

@bot.command()
async def snipe(ctx):
    cached = snipe_cache.get(str(ctx.channel.id))
    if not cached:
        return await ctx.send("🔍 Nothing to snipe in this channel.")
    age = int(time.time() - cached["time"])
    embed = discord.Embed(description=cached["content"], color=0xE74C3C, timestamp=datetime.datetime.utcnow())
    embed.set_author(name=cached["author"], icon_url=cached["avatar"])
    embed.set_footer(text=f"{'Deleted' if cached['type']=='deleted' else 'Edited'} • {age}s ago")
    await ctx.send(embed=embed)

@bot.command()
async def afk(ctx, *, reason: str = None):
    data = get_db(); ensure_user(data, ctx.author.id)
    uid = str(ctx.author.id)
    data[uid]["afk"] = reason or "AFK"
    afk_cache[uid]   = reason or "AFK"
    save_db(data)
    await ctx.send(f"💤 **{ctx.author.display_name}** is now AFK" + (f": *{reason}*" if reason else "."))

@bot.command()
async def partner(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id)
    u = data[str(t.id)]
    embed = discord.Embed(title=f"💍 {t.display_name}'s Marriage", color=0xFF69B4)
    if u.get("partner"):
        date = datetime.datetime.fromtimestamp(u["marry_date"]).strftime("%B %d, %Y")
        days = int((time.time() - u["marry_date"]) / 86400)
        embed.add_field(name="Partner",  value=f"<@{u['partner']}>")
        embed.add_field(name="Married",  value=date)
        embed.add_field(name="Together", value=f"{days} day(s)")
    else:
        embed.description = f"{'You are' if t==ctx.author else f'{t.display_name} is'} currently single 💔"
    await ctx.send(embed=embed)

@bot.command()
async def marry(ctx, member: discord.Member):
    if member == ctx.author or member.bot:
        return await ctx.send("❌ Invalid partner.")
    data = get_db()
    ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    if data[str(ctx.author.id)]["partner"]:
        return await ctx.send("❌ You're already married. Use `.divorce` first.")
    if data[str(member.id)]["partner"]:
        return await ctx.send(f"❌ {member.display_name} is already married.")

    class ProposeView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="💍 Accept", style=discord.ButtonStyle.success)
        async def yes(self, interaction, btn):
            if interaction.user != member:
                return await interaction.response.send_message("Not your proposal!", ephemeral=True)
            d = get_db(); now = time.time()
            d[str(ctx.author.id)]["partner"]    = member.id
            d[str(member.id)]["partner"]        = ctx.author.id
            d[str(ctx.author.id)]["marry_date"] = now
            d[str(member.id)]["marry_date"]     = now
            save_db(d)
            await interaction.response.edit_message(
                content=f"💍 **{ctx.author.display_name}** and **{member.display_name}** are now married! 🎊", view=None
            )

        @discord.ui.button(label="💔 Decline", style=discord.ButtonStyle.danger)
        async def no(self, interaction, btn):
            if interaction.user != member:
                return await interaction.response.send_message("Not your proposal!", ephemeral=True)
            await interaction.response.edit_message(content="💔 The proposal was declined.", view=None)

    await ctx.send(f"💍 {member.mention}, **{ctx.author.display_name}** is proposing to you!", view=ProposeView())

@bot.command()
async def divorce(ctx):
    data = get_db(); ensure_user(data, ctx.author.id)
    uid = str(ctx.author.id)
    pid = data[uid].get("partner")
    if not pid:
        return await ctx.send("❌ You're not married.")
    data[uid]["partner"] = None; data[uid]["marry_date"] = 0
    if str(pid) in data:
        data[str(pid)]["partner"] = None; data[str(pid)]["marry_date"] = 0
    save_db(data)
    await ctx.send("💔 You are now divorced.")

# ─────────────────────────────────────────────
# 8. BLACKTEA GAME
# ─────────────────────────────────────────────
blacktea_sessions = {}

BLACKTEA_WORDS = [
    "apple","brave","crane","dream","eagle","flame","grace","heart","ivory","jewel",
    "knack","lemon","magic","night","ocean","piano","queen","river","storm","tiger",
    "ultra","vivid","wheat","xenon","yacht","zebra","amber","bliss","chess","dance",
    "frost","globe","happy","inlet","jokes","karma","lunar","maple","novel","optic",
]

@bot.command()
async def blacktea(ctx):
    cid = str(ctx.channel.id)
    if cid in blacktea_sessions:
        return await ctx.send("⚠️ A Blacktea game is already running here.")
    word      = random.choice(BLACKTEA_WORDS)
    letters   = list(word); random.shuffle(letters)
    scrambled = " ".join(letters).upper()
    blacktea_sessions[cid] = {"word": word}

    embed = discord.Embed(
        title="🍵 Blacktea — Word Unscramble",
        description=f"Unscramble this word:\n\n**`{scrambled}`**\n\nType your answer in chat! **30 seconds.**",
        color=0x8B4513
    )
    embed.set_footer(text=f"Started by {ctx.author.display_name}")
    await ctx.send(embed=embed)

    def check(m):
        return m.channel == ctx.channel and not m.bot

    try:
        while True:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if msg.content.lower().strip() == word:
                data = get_db(); ensure_user(data, msg.author.id)
                data[str(msg.author.id)]["blacktea_wins"] += 1
                save_db(data)
                del blacktea_sessions[cid]
                return await ctx.send(f"🎉 **{msg.author.display_name}** got it! The word was **{word}**! (+1 Blacktea win)")
    except asyncio.TimeoutError:
        blacktea_sessions.pop(cid, None)
        await ctx.send(f"⏱️ Time's up! The word was **{word}**.")

# ─────────────────────────────────────────────
# 9. ECONOMY COMMANDS
# ─────────────────────────────────────────────
@bot.command(aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id)
    u = data[str(t.id)]
    embed = discord.Embed(title=f"💸 {t.display_name}'s Balance", color=0x2ECC71)
    embed.set_thumbnail(url=t.display_avatar.url)
    embed.add_field(name="👛 Wallet", value=f"${u['wallet']:,}",            inline=True)
    embed.add_field(name="🏦 Bank",   value=f"${u['bank']:,}",              inline=True)
    embed.add_field(name="💰 Total",  value=f"${u['wallet']+u['bank']:,}",  inline=True)
    await ctx.send(embed=embed)

@bot.command(aliases=["dep"])
async def deposit(ctx, amount: str):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    val = parse_amount(amount, data[uid]["wallet"])
    if not val or val <= 0 or val > data[uid]["wallet"]:
        return await ctx.send("❌ Invalid amount or not enough in wallet.")
    data[uid]["wallet"] -= val; data[uid]["bank"] += val
    save_db(data)
    await ctx.send(f"✅ Deposited **${val:,}** to your bank.")

@bot.command(aliases=["with"])
async def withdraw(ctx, amount: str):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    val = parse_amount(amount, data[uid]["bank"])
    if not val or val <= 0 or val > data[uid]["bank"]:
        return await ctx.send("❌ Invalid amount or not enough in bank.")
    data[uid]["bank"] -= val; data[uid]["wallet"] += val
    save_db(data)
    await ctx.send(f"🏧 Withdrew **${val:,}** to your wallet.")

@bot.command(aliases=["pay"])
async def give(ctx, member: discord.Member, amount: str):
    if member == ctx.author or member.bot:
        return await ctx.send("❌ Invalid recipient.")
    data = get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    val = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not val or val <= 0 or val > data[str(ctx.author.id)]["wallet"]:
        return await ctx.send("❌ Invalid amount.")
    data[str(ctx.author.id)]["wallet"] -= val
    data[str(member.id)]["wallet"]     += val
    save_db(data)
    await ctx.send(f"💸 **{ctx.author.display_name}** gave **${val:,}** to **{member.display_name}**.")

@bot.command()
@commands.cooldown(1, 36, commands.BucketType.user)
async def work(ctx):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    jobs = ["programmer","chef","taxi driver","streamer","delivery driver","barista","teacher","nurse"]
    pay  = random.randint(500, 1500) * get_multiplier(data, ctx.author.id)
    data[uid]["wallet"] += pay
    # store timestamp for cooldowns command
    data[uid]["last_work"] = time.time()
    save_db(data)
    await ctx.send(f"💼 You worked as a **{random.choice(jobs)}** and earned **${pay:,}**!")

@bot.command()
@commands.cooldown(1, 86400, commands.BucketType.user)
async def daily(ctx):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    reward = 2500 * get_multiplier(data, ctx.author.id)
    data[uid]["wallet"]     += reward
    data[uid]["last_daily"]  = time.time()
    save_db(data)
    await ctx.send(f"🎁 Daily claimed! **+${reward:,}**")

@bot.command()
@commands.cooldown(1, 604800, commands.BucketType.user)
async def weekly(ctx):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    reward = 15000 * get_multiplier(data, ctx.author.id)
    data[uid]["wallet"]      += reward
    data[uid]["last_weekly"]  = time.time()
    save_db(data)
    await ctx.send(f"🎁 Weekly claimed! **+${reward:,}**")

@bot.command()
async def cooldowns(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id); u = data[str(t.id)]
    now = time.time()

    def fmt(remaining):
        if remaining <= 0: return "✅ Ready"
        return f"⏱️ {str(datetime.timedelta(seconds=int(remaining)))}"

    embed = discord.Embed(title=f"⏱️ {t.display_name}'s Cooldowns", color=0xE67E22)
    embed.add_field(name="💼 Work",   value=fmt(36     - (now - u.get("last_work",  0))), inline=True)
    embed.add_field(name="🎁 Daily",  value=fmt(86400  - (now - u.get("last_daily", 0))), inline=True)
    embed.add_field(name="🎁 Weekly", value=fmt(604800 - (now - u.get("last_weekly",0))), inline=True)
    embed.add_field(name="🥷 Rob",    value=fmt(7200   - (now - u.get("last_rob",   0))), inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def inbox(ctx, page: int = 1):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    msgs = data[uid].get("inbox", [])
    if not msgs:
        return await ctx.send("📭 Your inbox is empty.")
    per   = 5; page = max(1, page)
    total = max(1, (len(msgs)+per-1)//per)
    page  = min(page, total)
    slice_ = msgs[(page-1)*per : page*per]
    embed = discord.Embed(title="📬 Your Inbox", color=0x3498DB)
    for i, m in enumerate(slice_, start=(page-1)*per+1):
        embed.add_field(name=f"#{i} — {m.get('from','System')}", value=m.get("text","…"), inline=False)
    embed.set_footer(text=f"Page {page}/{total}")
    await ctx.send(embed=embed)

@bot.command()
async def rob(ctx, member: discord.Member):
    if member == ctx.author:
        return await ctx.send("❌ You can't rob yourself.")
    data = get_db()
    ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    uid = str(ctx.author.id); tid = str(member.id)

    cd_left = 7200 - (time.time() - data[uid].get("last_rob", 0))
    if cd_left > 0:
        return await ctx.send(f"⏱️ Rob cooldown! **{str(datetime.timedelta(seconds=int(cd_left)))}** remaining.")
    if data[tid]["wallet"] < 500:
        return await ctx.send("❌ They don't have enough in their wallet (need $500+).")

    data[uid]["last_rob"] = time.time()
    if random.randint(1, 100) <= 45:
        stolen = random.randint(100, max(100, int(data[tid]["wallet"] * 0.3)))
        data[uid]["wallet"] += stolen
        data[tid]["wallet"] -= stolen
        await ctx.send(f"🥷 Success! Stole **${stolen:,}** from {member.display_name}.")
    else:
        fine = min(1000, data[uid]["wallet"])
        data[uid]["wallet"] = max(0, data[uid]["wallet"] - fine)
        await ctx.send(f"🚓 Busted! Paid a **${fine:,}** fine.")
    save_db(data)

# ─────────────────────────────────────────────
# 10. GAMBLING
# ─────────────────────────────────────────────
@bot.command(aliases=["cf"])
async def coinflip(ctx, amount: str, side: str):
    side = side.lower()
    if side not in ("heads","tails","h","t"):
        return await ctx.send("❌ Choose `heads` or `tails`.")
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]["wallet"])
    if not bet or bet <= 0 or bet > data[uid]["wallet"]:
        return await ctx.send("❌ Invalid bet or insufficient funds.")
    data[uid]["wallet"] -= bet
    result = random.choice(["heads","tails"])
    if side[0] == result[0]:
        win = bet * get_multiplier(data, ctx.author.id)
        data[uid]["wallet"] += bet + win
        msg = f"🪙 **{result.upper()}!** You won **${win:,}**! 🎉"
    else:
        msg = f"🪙 **{result.upper()}...** You lost **${bet:,}**."
    save_db(data); await ctx.send(msg)

# ── Blackjack ──
class BlackjackView(discord.ui.View):
    VALS  = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
    SUITS = ["♠","♥","♦","♣"]

    def __init__(self, ctx, bet, data):
        super().__init__(timeout=120)
        self.ctx = ctx; self.bet = bet; self.data = data
        deck = [f"{v}{s}" for s in self.SUITS for v in self.VALS] * 2
        random.shuffle(deck); self.deck = deck
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]

    def val(self, card):
        v = card[:-1]
        if v in ("J","Q","K"): return 10
        if v == "A":           return 11
        return int(v)

    def total(self, hand):
        t    = sum(self.val(c) for c in hand)
        aces = sum(1 for c in hand if c[:-1]=="A")
        while t > 21 and aces: t -= 10; aces -= 1
        return t

    def build_embed(self, ended=False, note=""):
        pt = self.total(self.player); dt = self.total(self.dealer)
        e  = discord.Embed(title="🃏 Blackjack", color=0x2ECC71)
        dealer_show = "  ".join(self.dealer) if ended else f"{self.dealer[0]}  🂠"
        e.add_field(name=f"Dealer {'('+str(dt)+')' if ended else ''}", value=dealer_show, inline=False)
        e.add_field(name=f"You ({pt})", value="  ".join(self.player), inline=False)
        e.add_field(name="Bet", value=f"${self.bet:,}", inline=True)
        if note: e.set_footer(text=note)
        return e

    async def resolve(self, interaction, status):
        uid  = str(self.ctx.author.id)
        mult = get_multiplier(self.data, self.ctx.author.id)
        if status == "win":
            win = self.bet * mult
            self.data[uid]["wallet"] += self.bet + win; note = f"🏆 You win! +${win:,}"
        elif status == "blackjack":
            win = int(self.bet * 1.5)
            self.data[uid]["wallet"] += self.bet + win; note = f"🃏 Blackjack! +${win:,}"
        elif status == "push":
            self.data[uid]["wallet"] += self.bet;       note = "🤝 Push — bet returned."
        else:
            note = f"😔 You lost ${self.bet:,}."
        save_db(self.data)
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(embed=self.build_embed(ended=True, note=note), view=self)

    @discord.ui.button(label="Hit",   style=discord.ButtonStyle.primary)
    async def hit(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.player.append(self.deck.pop())
        pt = self.total(self.player)
        if pt > 21: return await self.resolve(interaction, "lose")
        if pt == 21: return await self.stand_logic(interaction)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        await self.stand_logic(interaction)

    async def stand_logic(self, interaction):
        while self.total(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        p, d = self.total(self.player), self.total(self.dealer)
        if d > 21 or p > d: status = "win"
        elif p == d:         status = "push"
        else:                status = "lose"
        await self.resolve(interaction, status)

@bot.command(aliases=["bj"])
async def blackjack(ctx, amount: str):
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]["wallet"])
    if not bet or bet <= 0 or bet > data[uid]["wallet"]:
        return await ctx.send("❌ Invalid bet.")
    data[uid]["wallet"] -= bet; save_db(data)
    view = BlackjackView(ctx, bet, data)
    if view.total(view.player) == 21:
        win = int(bet * 1.5); data[uid]["wallet"] += bet + win; save_db(data)
        em = view.build_embed(ended=True, note=f"🃏 Blackjack! +${win:,}")
        return await ctx.send(embed=em)
    await ctx.send(embed=view.build_embed(), view=view)

# ── Roulette ──
_REDS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BETS = {
    "red":    (lambda n: n in _REDS,              2),
    "black":  (lambda n: n != 0 and n not in _REDS, 2),
    "even":   (lambda n: n != 0 and n%2==0,       2),
    "odd":    (lambda n: n%2==1,                  2),
    "low":    (lambda n: 1<=n<=18,                2),
    "high":   (lambda n: 19<=n<=36,               2),
    "dozen1": (lambda n: 1<=n<=12,                3),
    "dozen2": (lambda n: 13<=n<=24,               3),
    "dozen3": (lambda n: 25<=n<=36,               3),
}

@bot.command()
async def roulette(ctx, amount: str, bet_type: str):
    bt = bet_type.lower()
    if bt not in ROULETTE_BETS:
        return await ctx.send(f"❌ Types: {', '.join(f'`{k}`' for k in ROULETTE_BETS)}")
    data = get_db(); ensure_user(data, ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]["wallet"])
    if not bet or bet <= 0 or bet > data[uid]["wallet"]:
        return await ctx.send("❌ Invalid bet.")
    data[uid]["wallet"] -= bet
    spin = random.randint(0, 36)
    icon = "🔴" if spin in _REDS else ("🟩" if spin == 0 else "⚫")
    check_fn, mult = ROULETTE_BETS[bt]
    embed = discord.Embed(title="🎡 Roulette", color=0xC0392B)
    embed.add_field(name="Spin",     value=f"{icon} **{spin}**", inline=True)
    embed.add_field(name="Your Bet", value=f"`{bt}` — ${bet:,}", inline=True)
    if check_fn(spin):
        win = bet * (mult - 1) * get_multiplier(data, ctx.author.id)
        data[uid]["wallet"] += bet + win
        embed.add_field(name="Result", value=f"🎉 Win! **+${win:,}**", inline=False)
        embed.color = 0x2ECC71
    else:
        embed.add_field(name="Result", value=f"😔 Lost **${bet:,}**.", inline=False)
    save_db(data); await ctx.send(embed=embed)

# ── RPS ──
class RPSView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=60)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent
        self.bet = bet; self.data = data
        self.choices = {ctx.author.id: None, opponent.id: None}

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, btn):
        if interaction.user != self.opponent:
            return await interaction.response.send_message("Not your challenge!", ephemeral=True)
        self.clear_items()
        for c in ["Rock","Paper","Scissors"]:
            b = discord.ui.Button(label=c, custom_id=c.lower()); b.callback = self.pick; self.add_item(b)
        await interaction.response.edit_message(content="⚔️ Both players pick your move!", view=self)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, btn):
        if interaction.user != self.opponent:
            return await interaction.response.send_message("Not your challenge!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Duel declined.", view=None)

    async def pick(self, interaction):
        if interaction.user.id not in self.choices: return
        if self.choices[interaction.user.id]:
            return await interaction.response.send_message("Already picked!", ephemeral=True)
        self.choices[interaction.user.id] = interaction.data["custom_id"]
        await interaction.response.send_message(f"You picked **{interaction.data['custom_id']}**!", ephemeral=True)
        if all(v for v in self.choices.values()):
            beats = {"rock":"scissors","paper":"rock","scissors":"paper"}
            c, o = self.choices[self.challenger.id], self.choices[self.opponent.id]
            if c == o:
                result = "🤝 Draw! Bets returned."
            elif beats[c] == o:
                self.data[str(self.challenger.id)]["wallet"] += self.bet
                self.data[str(self.opponent.id)]["wallet"]   -= self.bet
                result = f"🏆 **{self.challenger.display_name}** wins **${self.bet:,}**! ({c} > {o})"
            else:
                self.data[str(self.opponent.id)]["wallet"]   += self.bet
                self.data[str(self.challenger.id)]["wallet"] -= self.bet
                result = f"🏆 **{self.opponent.display_name}** wins **${self.bet:,}**! ({o} > {c})"
            save_db(self.data)
            await interaction.message.edit(content=result, view=None)

@bot.command()
async def rps(ctx, member: discord.Member, amount: str):
    data = get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    if bet > data[str(ctx.author.id)]["wallet"]: return await ctx.send(f"❌ {ctx.author.display_name} can't afford that.")
    if bet > data[str(member.id)]["wallet"]:     return await ctx.send(f"❌ {member.display_name} can't afford that.")
    await ctx.send(f"⚔️ {member.mention}, **{ctx.author.display_name}** challenges you to RPS for **${bet:,}**!", view=RPSView(ctx, member, bet, data))

# ── 50/50 Duel ──
class DuelView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=60)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent
        self.bet = bet; self.data = data

    @discord.ui.button(label="⚔️ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, btn):
        if interaction.user != self.opponent:
            return await interaction.response.send_message("Not your duel!", ephemeral=True)
        winner = random.choice([self.challenger, self.opponent])
        loser  = self.opponent if winner == self.challenger else self.challenger
        self.data[str(winner.id)]["wallet"] += self.bet
        self.data[str(loser.id)]["wallet"]  -= self.bet
        save_db(self.data)
        await interaction.response.edit_message(
            content=f"⚔️ **{winner.display_name}** wins the duel and takes **${self.bet:,}**!", view=None
        )

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, btn):
        if interaction.user != self.opponent:
            return await interaction.response.send_message("Not your duel!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Duel declined.", view=None)

@bot.command()
async def duel(ctx, member: discord.Member, amount: str):
    data = get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    if bet > data[str(ctx.author.id)]["wallet"]: return await ctx.send(f"❌ {ctx.author.display_name} can't afford that.")
    if bet > data[str(member.id)]["wallet"]:     return await ctx.send(f"❌ {member.display_name} can't afford that.")
    await ctx.send(f"⚔️ {member.mention}, **{ctx.author.display_name}** challenges you to a duel for **${bet:,}**!", view=DuelView(ctx, member, bet, data))

# ── Tic-Tac-Toe ──
class TTTView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=120)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent
        self.bet = bet; self.data = data
        self.board = [None] * 9; self.turn = ctx.author; self.accepted = False

        ab = discord.ui.Button(label="✅ Accept", style=discord.ButtonStyle.success, custom_id="ttt_accept")
        ab.callback = self.do_accept; self.add_item(ab)
        db = discord.ui.Button(label="❌ Decline", style=discord.ButtonStyle.danger,  custom_id="ttt_decline")
        db.callback = self.do_decline; self.add_item(db)

    async def do_accept(self, interaction):
        if interaction.user != self.opponent:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.accepted = True; self.clear_items()
        for i in range(9):
            b = discord.ui.Button(label="⬜", style=discord.ButtonStyle.secondary, custom_id=f"ttt_{i}", row=i//3)
            b.callback = self.move; self.add_item(b)
        await interaction.response.edit_message(content=self.status(), view=self)

    async def do_decline(self, interaction):
        if interaction.user != self.opponent:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        await interaction.response.edit_message(content="❌ Game declined.", view=None)

    def status(self):
        return (f"❌ = {self.challenger.display_name}  |  ⭕ = {self.opponent.display_name}\n"
                f"🎯 **{self.turn.display_name}'s turn**")

    async def move(self, interaction):
        if not self.accepted: return
        if interaction.user != self.turn:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        idx = int(interaction.data["custom_id"].split("_")[1])
        if self.board[idx]: return
        self.board[idx] = self.turn
        sym = "❌" if self.turn == self.challenger else "⭕"
        for b in self.children:
            if getattr(b, "custom_id", "") == f"ttt_{idx}":
                b.label = sym; b.disabled = True
                b.style = discord.ButtonStyle.primary if sym=="❌" else discord.ButtonStyle.danger

        winner = self.check_winner()
        if winner:
            loser = self.opponent if winner == self.challenger else self.challenger
            self.data[str(winner.id)]["wallet"] += self.bet
            self.data[str(loser.id)]["wallet"]  -= self.bet
            save_db(self.data)
            for b in self.children: b.disabled = True
            return await interaction.response.edit_message(
                content=f"🏆 **{winner.display_name}** wins and takes **${self.bet:,}**!", view=self
            )
        if all(self.board):
            for b in self.children: b.disabled = True
            return await interaction.response.edit_message(content="🤝 Draw! Bets returned.", view=self)

        self.turn = self.opponent if self.turn == self.challenger else self.challenger
        await interaction.response.edit_message(content=self.status(), view=self)

    def check_winner(self):
        wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
        for a,b,c in wins:
            if self.board[a] and self.board[a]==self.board[b]==self.board[c]:
                return self.board[a]
        return None

@bot.command(aliases=["tictactoe"])
async def ttt(ctx, member: discord.Member, amount: str):
    data = get_db(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]["wallet"])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    if bet > data[str(ctx.author.id)]["wallet"]: return await ctx.send(f"❌ {ctx.author.display_name} can't afford that.")
    if bet > data[str(member.id)]["wallet"]:     return await ctx.send(f"❌ {member.display_name} can't afford that.")
    await ctx.send(f"🎮 {member.mention}, **{ctx.author.display_name}** challenges you to Tic-Tac-Toe for **${bet:,}**!", view=TTTView(ctx, member, bet, data))

# ─────────────────────────────────────────────
# 11. FUN COMMANDS
# ─────────────────────────────────────────────
_FUN = [
    ("hug",      discord.Color.from_rgb(255,182,193)),
    ("kiss",     discord.Color.red()),
    ("dance",    discord.Color.purple()),
    ("handhold", discord.Color.from_rgb(255,200,210)),
    ("cry",      discord.Color.blue()),
    ("bite",     discord.Color.dark_red()),
    ("poke",     discord.Color.orange()),
    ("lick",     discord.Color.from_rgb(255,220,100)),
    ("highfive", discord.Color.green()),
    ("slap",     discord.Color.dark_orange()),
    ("cuddle",   discord.Color.from_rgb(255,160,180)),
    ("kill",     discord.Color.dark_gray()),
]

for _action, _color in _FUN:
    def _make(action, color):
        @bot.command(name=action)
        async def _fun_cmd(ctx, member: discord.Member, _a=action, _c=color):
            await send_action(ctx, _a, member, _c)
        _fun_cmd.__name__ = action
    _make(_action, _color)

@bot.command()
async def bestie(ctx, member1: discord.Member, member2: discord.Member = None):
    a = member1; b = member2 or ctx.author
    pct  = abs(hash(f"{min(a.id,b.id)}{max(a.id,b.id)}")) % 101
    fill = int(pct / 5); bar = "█"*fill + "░"*(20-fill)
    note = "💕 Absolute besties!" if pct>=80 else ("😊 Pretty good friends!" if pct>=50 else "🤔 Could be better...")
    embed = discord.Embed(
        title="👯 Bestie Compatibility",
        description=f"**{a.display_name}** & **{b.display_name}**\n\n`{bar}` **{pct}%**",
        color=0xFF69B4
    )
    embed.set_footer(text=note)
    await ctx.send(embed=embed)

@bot.command()
async def aura(ctx, member: discord.Member = None):
    t = member or ctx.author
    power = abs(hash(str(t.id)+"aura")) % 1001
    if power >= 900:   label, color = "✨ Legendary Aura", 0xFFD700
    elif power >= 700: label, color = "🌟 Radiant Aura",  0xFFA500
    elif power >= 400: label, color = "💫 Neutral Aura",  0x3498DB
    else:              label, color = "🌑 Dark Aura",      0x2C3E50
    embed = discord.Embed(
        title=f"🔮 {t.display_name}'s Aura",
        description=f"**{label}**\nAura Power: **{power}/1000**",
        color=color
    )
    await ctx.send(embed=embed)

@bot.command()
async def ship(ctx, member1: discord.Member, member2: discord.Member = None):
    a = member1; b = member2 or ctx.author
    pct  = abs(hash(f"{min(a.id,b.id)}{max(a.id,b.id)}ship")) % 101
    bar  = "💗"*(pct//10) + "🤍"*(10-pct//10)
    name = a.display_name[:max(1,len(a.display_name)//2)] + b.display_name[len(b.display_name)//2:]
    note = "💍 Soulmates!" if pct>=90 else ("💕 Strong connection!" if pct>=60 else ("🙂 There's potential!" if pct>=30 else "💔 Not meant to be..."))
    embed = discord.Embed(
        title="💘 Ship Meter",
        description=f"**{a.display_name}** 💞 **{b.display_name}**\nShip name: **{name}**\n\n`{bar}` **{pct}%**",
        color=0xFF1493
    )
    embed.set_footer(text=note)
    await ctx.send(embed=embed)

# ─────────────────────────────────────────────
# LEVEL COMMAND — add this in the GENERAL COMMANDS section
# ─────────────────────────────────────────────

class LevelView(discord.ui.View):
    def __init__(self, ctx, target, data):
        super().__init__(timeout=120)
        self.ctx    = ctx
        self.target = target
        self.data   = data
        self.notif_server = False
        self.notif_dm     = False

    def progress_embed(self):
        u   = self.data[str(self.target.id)]
        lvl = u["level"]
        xp  = u["xp"]
        needed  = lvl * 500
        filled  = int((xp / needed) * 20) if needed else 0
        bar     = "█" * filled + "░" * (20 - filled)
        mult    = get_multiplier(self.data, self.target.id)
        prestige = u.get("prestige", 0)

        # Global rank
        sorted_users = sorted(
            self.data.items(),
            key=lambda x: (x[1].get("prestige", 0), x[1].get("level", 1), x[1].get("xp", 0)),
            reverse=True
        )
        rank = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == str(self.target.id)), "?")

        # Milestones
        milestones = [
            (5,   "📹 Streaming / Camera"),
            (10,  "🖼️ Media Channel Posting"),
            (20,  "😄 External Emojis"),
            (30,  "🎞️ GIFs"),
            (40,  "🎨 Color Panel"),
            (50,  "🗂️ External Stickers"),
            (60,  "📸 Post Images Anywhere"),
            (80,  "🔊 Soundboards"),
            (100, "⭐ Prestige"),
        ]

        milestone_lines = []
        for req_lvl, name in milestones:
            if lvl >= req_lvl:
                milestone_lines.append(f"✅ {name} *(Lvl {req_lvl})*")
            else:
                milestone_lines.append(f"❌ {name} *(Lvl {req_lvl})*")

        embed = discord.Embed(
            title=f"📊 {self.target.display_name}'s Progress",
            color=0x5865F2
        )
        embed.set_thumbnail(url=self.target.display_avatar.url)

        embed.add_field(
            name="⚡ Level & XP",
            value=(
                f"**Level:** {lvl}  •  **Prestige:** {prestige}\n"
                f"**XP Multiplier:** {mult}x\n"
                f"**Global Rank:** #{rank}\n"
                f"**Next Level:** {needed - xp:,} XP needed\n"
                f"`{bar}` {xp}/{needed}"
            ),
            inline=False
        )

        embed.add_field(
            name="🏆 Milestones",
            value="\n".join(milestone_lines),
            inline=False
        )

        embed.set_footer(text="💡 Earn XP by chatting in text channels or being active in VC!")
        return embed

    def credits_embed(self):
        u = self.data[str(self.target.id)]
        embed = discord.Embed(title=f"🪙 {self.target.display_name}'s Credits", color=0xF1C40F)
        embed.add_field(name="Credits", value=f"**{u['credits']:,}**")
        embed.set_footer(text="Earn credits by chatting every 30s (+5 each time)")
        return embed

    def boosters_embed(self):
        u   = self.data[str(self.target.id)]
        now = time.time()
        rem = u.get("booster_end", 0) - now
        embed = discord.Embed(title=f"🚀 {self.target.display_name}'s Boosters", color=0x9B59B6)
        if rem > 0:
            embed.add_field(name="2x Booster", value=f"Expires in **{str(datetime.timedelta(seconds=int(rem)))}**")
        else:
            embed.description = "No active boosters."
        return embed

    @discord.ui.button(label="Progress", style=discord.ButtonStyle.primary, emoji="📊")
    async def progress_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.edit_message(embed=self.progress_embed(), view=self)

    @discord.ui.button(label="Credits", style=discord.ButtonStyle.secondary, emoji="🪙")
    async def credits_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.edit_message(embed=self.credits_embed(), view=self)

    @discord.ui.button(label="Boosters", style=discord.ButtonStyle.secondary, emoji="🚀")
    async def boosters_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.edit_message(embed=self.boosters_embed(), view=self)

    @discord.ui.button(label="Equip Tag", style=discord.ButtonStyle.success, emoji="🏷️")
    async def equip_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        await interaction.response.send_message(
            "🏷️ To equip your server tag, go to **Server Settings → Members → Your Profile** and select the tag.",
            ephemeral=True
        )

    @discord.ui.button(label="🔔 Server Notifs", style=discord.ButtonStyle.secondary, row=1)
    async def server_notif_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        self.notif_server = not self.notif_server
        btn.style = discord.ButtonStyle.success if self.notif_server else discord.ButtonStyle.secondary
        btn.label = "🔔 Server Notifs ON" if self.notif_server else "🔔 Server Notifs"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="📩 DM Notifs", style=discord.ButtonStyle.secondary, row=1)
    async def dm_notif_btn(self, interaction, btn):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your menu!", ephemeral=True)
        self.notif_dm = not self.notif_dm
        btn.style = discord.ButtonStyle.success if self.notif_dm else discord.ButtonStyle.secondary
        btn.label = "📩 DM Notifs ON" if self.notif_dm else "📩 DM Notifs"
        await interaction.response.edit_message(view=self)


@bot.command(aliases=["lvl", "level"])
async def progress(ctx, member: discord.Member = None):
    t = member or ctx.author
    data = get_db(); ensure_user(data, t.id)
    view = LevelView(ctx, t, data)
    await ctx.send(embed=view.progress_embed(), view=view)

# ─────────────────────────────────────────────
# GUIDELINES & PROFILE SETUP COMMANDS
# Add these to your main.py
# ─────────────────────────────────────────────

# Color roles — create these in your Discord server
COLOR_ROLES = {
    # Red/Pink
    "Scarlet Fury":      "🔴",
    "Fire Pop":          "🟠",
    "Rose Dust":         "🌸",
    "Crimson Blaze":     "❤️",
    "Raspberry Burst":   "🍇",
    "Blush Bloom":       "🌷",
    # Yellow/Orange
    "Golden Ember":      "🟡",
    "Sunbeam Honey":     "🍯",
    "Apricot Glow":      "🍑",
    # Green
    "Emerald Surge":     "💚",
    "Mint Breeze":       "🌿",
    "Frosted Mist":      "🩵",
    # Blue
    "Ocean Depth":       "🌊",
}

# ── Guidelines Embed ──
async def post_guidelines(channel):
    embed = discord.Embed(
        title="📋  Hang Spot — Guidelines",
        description=(
            "Welcome to **Hang Spot** 🍄\n"
            "An **active and chill** community with emotes and daily giveaways.\n"
            "Please read and respect the rules below before participating.\n\n"
            "**Owner:** EN1SSAY\n"
        ),
        color=0xFF85A1
    )

    embed.add_field(
        name="1️⃣  Discord TOS",
        value="Follow [Discord's Terms of Service](https://discord.com/terms) and Community Guidelines at all times.",
        inline=False
    )
    embed.add_field(
        name="2️⃣  Be Respectful",
        value="Hate speech, harassment, sexism, racism, and doxing are **strictly forbidden** and will result in an immediate ban.",
        inline=False
    )
    embed.add_field(
        name="3️⃣  SFW Only",
        value="This server is **strictly Safe For Work**. No NSFW content of any kind. No e-dating.",
        inline=False
    )
    embed.add_field(
        name="4️⃣  No Advertising",
        value="Unauthorized promotion, server links, or poaching of members is **not allowed**.",
        inline=False
    )
    embed.add_field(
        name="5️⃣  Staff Discretion",
        value="Staff may take action without prior warning. Follow staff instructions without argument.",
        inline=False
    )
    embed.set_footer(text="By participating in this server you agree to these rules.")

    class GuidelinesView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="Get your roles here", style=discord.ButtonStyle.primary, emoji="🌹", custom_id="goto_profile")
        async def profile_btn(self, interaction, btn):
            await interaction.response.send_message(
                "Head over to <#1482802311578259497> to get your roles!",
                ephemeral=True
            )

        @discord.ui.button(label="Server Perks", style=discord.ButtonStyle.secondary, emoji="🎲", custom_id="goto_perks")
        async def perks_btn(self, interaction, btn):
            await interaction.response.send_message(
                "Check out <#1482802267680804964> to see all server perks!",
                ephemeral=True
            )

    await channel.send(embed=embed, view=GuidelinesView())


# ── Profile / Role Selection ──
class GenderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="1. Female", style=discord.ButtonStyle.secondary, emoji="🌸", custom_id="role_female")
    async def female_btn(self, interaction, btn):
        role = discord.utils.get(interaction.guild.roles, name="Female")
        male_role = discord.utils.get(interaction.guild.roles, name="Male")
        if not role:
            return await interaction.response.send_message("❌ `Female` role not found. Ask an admin to create it.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            return await interaction.response.send_message("🌸 Removed your **Female** role.", ephemeral=True)
        if male_role and male_role in interaction.user.roles:
            await interaction.user.remove_roles(male_role)
        await interaction.user.add_roles(role)
        await interaction.response.send_message("🌸 You now have the **Female** role!", ephemeral=True)

    @discord.ui.button(label="2. Male", style=discord.ButtonStyle.secondary, emoji="💙", custom_id="role_male")
    async def male_btn(self, interaction, btn):
        role = discord.utils.get(interaction.guild.roles, name="Male")
        female_role = discord.utils.get(interaction.guild.roles, name="Female")
        if not role:
            return await interaction.response.send_message("❌ `Male` role not found. Ask an admin to create it.", ephemeral=True)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            return await interaction.response.send_message("💙 Removed your **Male** role.", ephemeral=True)
        if female_role and female_role in interaction.user.roles:
            await interaction.user.remove_roles(female_role)
        await interaction.user.add_roles(role)
        await interaction.response.send_message("💙 You now have the **Male** role!", ephemeral=True)


class ColorSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, emoji=emoji, value=name)
            for name, emoji in COLOR_ROLES.items()
        ]
        super().__init__(
            placeholder="🎨 Choose your color...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="color_select"
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        # Remove all other color roles first
        roles_to_remove = [
            discord.utils.get(interaction.guild.roles, name=name)
            for name in COLOR_ROLES
        ]
        roles_to_remove = [r for r in roles_to_remove if r and r in interaction.user.roles]
        if roles_to_remove:
            await interaction.user.remove_roles(*roles_to_remove)

        # Add chosen role
        new_role = discord.utils.get(interaction.guild.roles, name=chosen)
        if not new_role:
            return await interaction.response.send_message(
                f"❌ Role `{chosen}` not found. Ask an admin to create it.", ephemeral=True
            )
        await interaction.user.add_roles(new_role)
        emoji = COLOR_ROLES[chosen]
        await interaction.response.send_message(
            f"{emoji} You now have the **{chosen}** color role!", ephemeral=True
        )


class ColorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ColorSelect())


async def post_profile(channel):
    # Gender selection embed
    gender_embed = discord.Embed(
        title="💮  Gender Roles",
        description=(
            "Pick your gender role below.\n"
            "Click again to **remove** it.\n\n"
            "**1.** 🌸 Female\n"
            "**2.** 💙 Male"
        ),
        color=0xFF85A1
    )
    await channel.send(embed=gender_embed, view=GenderView())

    # Color selection embed
    color_embed = discord.Embed(
        title="🎨  Color Roles",
        description=(
            "Choose your name color from the dropdown below!\n\n"
            "**Red / Pink**\n"
            "🔴 Scarlet Fury  •  🟠 Fire Pop  •  🌸 Rose Dust\n"
            "❤️ Crimson Blaze  •  🍇 Raspberry Burst  •  🌷 Blush Bloom\n\n"
            "**Yellow / Orange**\n"
            "🟡 Golden Ember  •  🍯 Sunbeam Honey  •  🍑 Apricot Glow\n\n"
            "**Green**\n"
            "💚 Emerald Surge  •  🌿 Mint Breeze  •  🩵 Frosted Mist\n\n"
            "**Blue**\n"
            "🌊 Ocean Depth"
        ),
        color=0x5865F2
    )
    await channel.send(embed=color_embed, view=ColorView())


# ── Admin Setup Commands ──

@bot.command()
@commands.has_permissions(administrator=True)
async def setupguidelines(ctx):
    """Post the guidelines embed in the current channel."""
    await post_guidelines(ctx.channel)
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setupprofile(ctx):
    """Post the profile/role selection embed in the current channel."""
    await post_profile(ctx.channel)
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setupall(ctx, guidelines_channel: discord.TextChannel, profile_channel: discord.TextChannel):
    """Post everything in the correct channels at once.
    Usage: .setupall #guidelines #profile
    """
    await post_guidelines(guidelines_channel)
    await post_profile(profile_channel)
    await ctx.send(f"✅ Guidelines posted in {guidelines_channel.mention} and profile in {profile_channel.mention}.")

# ─────────────────────────────────────────────
# 12. START
# ─────────────────────────────────────────────
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ TOKEN NOT FOUND! Check your .env file.")
