import os
import random
import json
import datetime
import time
from dotenv import load_dotenv
import discord
from discord.ext import commands

# --- 1. CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv('TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix='.', intents=intents)
msg_cooldown = {} 

# --- 2. DATABASE FUNCTIONS ---
DB_FILE = 'users.json'

def get_user_data():
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} 

def save_user_data(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def ensure_user(data, user_id):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    
    defaults = {
        'wallet': 0, 'bank': 0, 'credits': 0, 'xp': 0, 'level': 1, 'booster_end': 0,
        'last_daily': 0, 'last_work': 0, 'last_rob': 0, 'last_heist': 0,
        'partner': None, 'marry_date': 0
    }
    for key, value in defaults.items():
        if key not in data[uid]:
            data[uid][key] = value
    return data

def parse_amount(amount_str, balance):
    amount_str = amount_str.lower().strip()
    if amount_str == 'all': return balance
    elif amount_str == 'half': return balance // 2
    multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
    if amount_str[-1] in multipliers:
        try: return int(float(amount_str[:-1]) * multipliers[amount_str[-1]])
        except: return None
    try: return int(amount_str)
    except: return None

def get_multiplier(data, user_id):
    uid = str(user_id)
    return 2 if data[uid].get('booster_end', 0) > time.time() else 1

def add_xp(data, user_id, amount):
    uid = str(user_id)
    data[uid]['xp'] += amount
    needed = data[uid]['level'] * 500
    if data[uid]['xp'] >= needed:
        data[uid]['level'] += 1
        data[uid]['xp'] = 0
        return True
    return False

# --- 3. UI COMPONENTS ---

class LeaderboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    def create_embed(self, category):
        data = get_user_data()
        embed = discord.Embed(color=discord.Color.gold())
        
        if category == "economy":
            embed.title = "💰 Economy Leaderboard"
            sorted_list = sorted(data.items(), key=lambda x: x[1].get('wallet', 0) + x[1].get('bank', 0), reverse=True)
            desc = "\n".join([f"**{i+1}.** <@{u}> — ${s.get('wallet',0)+s.get('bank',0):,}" for i, (u, s) in enumerate(sorted_list[:10])])
        
        elif category == "levels":
            embed.title = "📊 Level Leaderboard"
            sorted_list = sorted(data.items(), key=lambda x: (x[1].get('level', 1), x[1].get('xp', 0)), reverse=True)
            desc = "\n".join([f"**{i+1}.** <@{u}> — Lvl {s.get('level',1)} ({s.get('xp',0)} XP)" for i, (u, s) in enumerate(sorted_list[:10])])
        
        elif category == "marriage":
            embed.title = "💖 Marriage Leaderboard"
            married = [x for x in data.items() if x[1].get('partner')]
            sorted_list = sorted(married, key=lambda x: x[1].get('marry_date', 0))
            desc = "\n".join([f"**{i+1}.** <@{u}> & <@{s['partner']}>" for i, (u, s) in enumerate(sorted_list[:10])])
        
        embed.description = desc if desc else "No data found."
        return embed

    @discord.ui.button(label="Economy", style=discord.ButtonStyle.primary)
    async def eco_btn(self, interaction, button):
        await interaction.response.edit_message(embed=self.create_embed("economy"), view=self)

    @discord.ui.button(label="Levels", style=discord.ButtonStyle.secondary)
    async def lvl_btn(self, interaction, button):
        await interaction.response.edit_message(embed=self.create_embed("levels"), view=self)

    @discord.ui.button(label="Marriage", style=discord.ButtonStyle.secondary)
    async def marry_btn(self, interaction, button):
        await interaction.response.edit_message(embed=self.create_embed("marriage"), view=self)

class MinesView(discord.ui.View):
    def __init__(self, ctx, bet, data):
        super().__init__(timeout=120)
        self.ctx = ctx; self.bet = bet; self.data = data
        self.grid = ['safe'] * 9; self.grid[random.randint(0, 8)] = 'bomb'
        self.revealed = 0
        for i in range(9):
            btn = discord.ui.Button(label="?", style=discord.ButtonStyle.secondary, custom_id=str(i), row=i//3)
            btn.callback = self.press; self.add_item(btn)
        self.cashout = discord.ui.Button(label="Cashout (1.0x)", style=discord.ButtonStyle.success, row=3)
        self.cashout.callback = self.finish; self.add_item(self.cashout)

    async def press(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author: return
        idx = int(interaction.data['custom_id'])
        if self.grid[idx] == 'bomb':
            self.data[str(self.ctx.author.id)]['wallet'] -= self.bet; save_user_data(self.data)
            return await interaction.response.edit_message(content=f"💥 **BOMB!** Lost ${self.bet:,}", view=None)
        self.revealed += 1
        for b in self.children: 
            if b.custom_id == str(idx): b.disabled = True; b.label = "💎"
        mult = [1.0, 1.2, 1.5, 1.9, 2.4, 3.1, 4.2, 6.0, 12.0][self.revealed]
        self.cashout.label = f"Cashout ({mult}x)"
        await interaction.response.edit_message(view=self)

    async def finish(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author: return
        mult = [1.0, 1.2, 1.5, 1.9, 2.4, 3.1, 4.2, 6.0, 12.0][self.revealed]
        win = int(self.bet * mult * get_multiplier(self.data, self.ctx.author.id))
        self.data[str(self.ctx.author.id)]['wallet'] += (win - self.bet); save_user_data(self.data)
        await interaction.response.edit_message(content=f"💰 **Win!** You took home ${win:,}!", view=None)

class RPSDuelView(discord.ui.View):
    def __init__(self, ctx, opponent, bet, data):
        super().__init__(timeout=60)
        self.ctx = ctx; self.challenger = ctx.author; self.opponent = opponent; self.bet = bet; self.data = data
        self.choices = {self.challenger.id: None, self.opponent.id: None}

    @discord.ui.button(label="Accept Duel", style=discord.ButtonStyle.success)
    async def accept(self, interaction, btn):
        if interaction.user != self.opponent: return
        self.clear_items()
        for choice in ["Rock", "Paper", "Scissors"]:
            b = discord.ui.Button(label=choice, custom_id=choice.lower()); b.callback = self.play; self.add_item(b)
        await interaction.response.edit_message(content="⚔️ Pick your move!", view=self)

    async def play(self, interaction):
        if interaction.user.id not in self.choices or self.choices[interaction.user.id]: return
        self.choices[interaction.user.id] = interaction.data['custom_id']
        await interaction.response.send_message(f"You picked {interaction.data['custom_id']}!", ephemeral=True)
        if all(self.choices.values()):
            c, o = self.choices[self.challenger.id], self.choices[self.opponent.id]
            win_map = {'rock': 'scissors', 'paper': 'rock', 'scissors': 'paper'}
            if c == o: res = "🤝 Draw!"
            elif win_map[c] == o:
                self.data[str(self.challenger.id)]['wallet'] += self.bet; self.data[str(self.opponent.id)]['wallet'] -= self.bet
                res = f"🏆 {self.challenger.name} won ${self.bet:,}!"
            else:
                self.data[str(self.opponent.id)]['wallet'] += self.bet; self.data[str(self.challenger.id)]['wallet'] -= self.bet
                res = f"🏆 {self.opponent.name} won ${self.bet:,}!"
            save_user_data(self.data)
            await interaction.message.edit(content=res, view=None)

# --- 4. BOT EVENTS & COMMANDS ---

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot: return
    uid = str(message.author.id); now = time.time()
    
    # Message XP & Credits System
    if uid not in msg_cooldown or now - msg_cooldown[uid] > 30:
        data = get_user_data(); ensure_user(data, message.author.id)
        data[uid]['credits'] += 5
        if add_xp(data, message.author.id, 20):
            try: await message.channel.send(f"🎊 {message.author.mention} leveled up to **Level {data[uid]['level']}**!")
            except: pass
        save_user_data(data); msg_cooldown[uid] = now
        
    await bot.process_commands(message)

@bot.command(aliases=['lb'])
async def leaderboard(ctx, category: str = "economy"):
    view = LeaderboardView()
    cat = "economy"
    if category.lower() in ['levels', 'lvl']: cat = "levels"
    if category.lower() in ['marriage', 'marry']: cat = "marriage"
    await ctx.send(embed=view.create_embed(cat), view=view)

@bot.command(aliases=['bal'])
async def balance(ctx, member: discord.Member = None):
    t = member if member else ctx.author
    data = ensure_user(get_user_data(), t.id); u = data[str(t.id)]
    embed = discord.Embed(title=f"💸 {t.name}'s Balance", color=discord.Color.blue())
    embed.add_field(name="Wallet", value=f"${u['wallet']:,}", inline=True)
    embed.add_field(name="Bank", value=f"${u['bank']:,}", inline=True)
    await ctx.send(embed=embed)

@bot.command(aliases=['dep'])
async def deposit(ctx, amount: str):
    data = ensure_user(get_user_data(), ctx.author.id); uid = str(ctx.author.id)
    val = parse_amount(amount, data[uid]['wallet'])
    if not val or val <= 0 or val > data[uid]['wallet']: return await ctx.send("❌ Invalid amount.")
    data[uid]['wallet'] -= val; data[uid]['bank'] += val
    save_user_data(data); await ctx.send(f"✅ Deposited **${val:,}**")

@bot.command(aliases=['with', 'w'])
async def withdraw(ctx, amount: str):
    data = ensure_user(get_user_data(), ctx.author.id); uid = str(ctx.author.id)
    val = parse_amount(amount, data[uid]['bank'])
    if not val or val <= 0 or val > data[uid]['bank']: return await ctx.send("❌ Invalid amount.")
    data[uid]['bank'] -= val; data[uid]['wallet'] += val
    save_user_data(data); await ctx.send(f"🏧 Withdrew **${val:,}**")

@bot.command(aliases=['pay'])
async def give(ctx, member: discord.Member, amount: str):
    if member == ctx.author or member.bot: return await ctx.send("❌ Invalid user.")
    data = get_user_data(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    val = parse_amount(amount, data[str(ctx.author.id)]['wallet'])
    if not val or val <= 0 or val > data[str(ctx.author.id)]['wallet']: return await ctx.send("❌ Invalid amount.")
    data[str(ctx.author.id)]['wallet'] -= val; data[str(member.id)]['wallet'] += val
    save_user_data(data); await ctx.send(f"💸 Gave **${val:,}** to {member.name}")

@bot.command()
async def work(ctx):
    data = ensure_user(get_user_data(), ctx.author.id); uid = str(ctx.author.id)
    if time.time() - data[uid]['last_work'] < 36: 
        rem = int(36 - (time.time() - data[uid]['last_work']))
        return await ctx.send(f"👷 Cooldown: **{rem}s**.")
    pay = random.randint(500, 1500) * get_multiplier(data, ctx.author.id)
    data[uid]['wallet'] += pay; data[uid]['last_work'] = time.time()
    save_user_data(data); await ctx.send(f"💼 Earned **${pay:,}**")

@bot.command()
async def daily(ctx):
    data = ensure_user(get_user_data(), ctx.author.id); uid = str(ctx.author.id)
    if time.time() - data[uid]['last_daily'] < 86400: return await ctx.send("⏱️ 24h cooldown.")
    reward = 2500 * get_multiplier(data, ctx.author.id)
    data[uid]['wallet'] += reward; data[uid]['last_daily'] = time.time()
    save_user_data(data); await ctx.send(f"🎁 Claimed **${reward:,}**!")

@bot.command(aliases=['cf'])
async def coinflip(ctx, side: str, amount: str):
    side = side.lower(); data = ensure_user(get_user_data(), ctx.author.id); uid = str(ctx.author.id)
    bet = parse_amount(amount, data[uid]['wallet'])
    if not bet or bet <= 0: return await ctx.send("❌ Invalid bet.")
    res = random.choice(['heads', 'tails'])
    if side.startswith(res[0]):
        win = bet * get_multiplier(data, ctx.author.id)
        data[uid]['wallet'] += win; await ctx.send(f"🪙 **{res.upper()}**! Won **${win:,}**")
    else:
        data[uid]['wallet'] -= bet; await ctx.send(f"🪙 **{res.upper()}**... Lost **${bet:,}**")
    save_user_data(data)

@bot.command()
async def mines(ctx, amount: str):
    data = ensure_user(get_user_data(), ctx.author.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]['wallet'])
    if not bet or bet > data[str(ctx.author.id)]['wallet']: return await ctx.send("❌ Too poor.")
    await ctx.send(f"💣 **Mines** | Bet: ${bet:,}", view=MinesView(ctx, bet, data))

@bot.command()
async def rps(ctx, member: discord.Member, amount: str):
    data = get_user_data(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    bet = parse_amount(amount, data[str(ctx.author.id)]['wallet'])
    if not bet or bet > data[str(ctx.author.id)]['wallet'] or bet > data[str(member.id)]['wallet']:
        return await ctx.send("❌ One of you lacks the funds.")
    await ctx.send(f"⚔️ {member.mention}, challenge from {ctx.author.name} for ${bet:,}!", view=RPSDuelView(ctx, member, bet, data))

@bot.command()
async def rob(ctx, member: discord.Member):
    if member == ctx.author: return
    data = get_user_data(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    
    # Wealth-Ratio Protection (The "Anti-Bully" Rule)
    if data[str(ctx.author.id)]['bank'] > (data[str(member.id)]['bank'] * 10):
        return await ctx.send("⚖️ Target is too poor for your status! You can't rob people with <10% of your bank.")

    if time.time() - data[str(ctx.author.id)]['last_rob'] < 7200: return await ctx.send("⏱️ 2h Cooldown.")
    if data[str(member.id)]['wallet'] < 500: return await ctx.send("❌ Target is too broke.")
    
    data[str(ctx.author.id)]['last_rob'] = time.time()
    if random.randint(1, 100) < 40:
        stolen = random.randint(100, int(data[str(member.id)]['wallet'] * 0.3))
        data[str(ctx.author.id)]['wallet'] += stolen; data[str(member.id)]['wallet'] -= stolen
        await ctx.send(f"🥷 Stole **${stolen:,}**!")
    else:
        fine = 1000; data[str(ctx.author.id)]['wallet'] -= fine
        await ctx.send(f"🚓 Busted! Paid a **$1,000** fine.")
    save_user_data(data)

@bot.command()
async def heist(ctx, member: discord.Member):
    if member == ctx.author: return
    data = get_user_data(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    if time.time() - data[str(ctx.author.id)]['last_heist'] < 18000: return await ctx.send("⏱️ 5h Cooldown.")
    if data[str(member.id)]['bank'] < 5000: return await ctx.send("❌ Vault is empty.")
    
    data[str(ctx.author.id)]['last_heist'] = time.time()
    if random.randint(1, 100) < 30:
        stolen = int(data[str(member.id)]['bank'] * 0.5)
        data[str(ctx.author.id)]['wallet'] += stolen; data[str(member.id)]['bank'] -= stolen
        await ctx.send(f"🏦 VAULT CRACKED! Took **${stolen:,}**!")
    else:
        fine = int(data[str(ctx.author.id)]['bank'] * 0.2); data[str(ctx.author.id)]['bank'] -= fine
        await ctx.send(f"🚓 BUSTED! Lost **${fine:,}** from bank.")
    save_user_data(data)

@bot.command()
async def marry(ctx, member: discord.Member):
    if member == ctx.author or member.bot: return
    data = get_user_data(); ensure_user(data, ctx.author.id); ensure_user(data, member.id)
    if data[str(ctx.author.id)]['partner']: return await ctx.send("❌ Already married.")
    if data[str(member.id)]['partner']: return await ctx.send("❌ They are already married.")
    
    class Propose(discord.ui.View):
        def __init__(self): super().__init__(timeout=60)
        @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
        async def yes(self, i, b):
            if i.user != member: return
            d = get_user_data(); now = time.time()
            d[str(ctx.author.id)]['partner'] = member.id; d[str(member.id)]['partner'] = ctx.author.id
            d[str(ctx.author.id)]['marry_date'] = now; d[str(member.id)]['marry_date'] = now
            save_user_data(d); await i.response.edit_message(content="💍 Married!", view=None)
        @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
        async def no(self, i, b):
            if i.user != member: return
            await i.response.edit_message(content="💔 Declined.", view=None)

    await ctx.send(f"💍 {member.mention}, proposal from {ctx.author.name}!", view=Propose())

@bot.command()
async def divorce(ctx):
    data = get_user_data(); uid = str(ctx.author.id)
    partner = data[uid].get('partner')
    if not partner: return await ctx.send("❌ Not married.")
    data[uid]['partner'] = None; data[str(partner)]['partner'] = None
    save_user_data(data); await ctx.send("💔 Divorced.")

@bot.command()
async def profile(ctx, member: discord.Member = None):
    t = member if member else ctx.author; data = ensure_user(get_user_data(), t.id); u = data[str(t.id)]
    embed = discord.Embed(title=f"👤 {t.name}'s Profile", color=discord.Color.purple())
    embed.add_field(name="Level", value=f"Lvl {u['level']} ({u['xp']}/{u['level']*500} XP)")
    embed.add_field(name="Credits", value=f"{u['credits']:,}")
    rem = u['booster_end'] - time.time()
    embed.add_field(name="Booster", value=str(datetime.timedelta(seconds=int(rem))) if rem > 0 else "None")
    p_id = u['partner']
    partner_str = f"<@{p_id}>" if p_id else "Single"
    embed.add_field(name="Partner", value=partner_str)
    await ctx.send(embed=embed)

@bot.command()
async def buy(ctx, item: str = None):
    if item != "x2": return await ctx.send("🛒 Use `.buy x2` (500 Credits) for a 24h multiplier.")
    data = get_user_data(); uid = str(ctx.author.id)
    if data[uid]['credits'] < 500: return await ctx.send("❌ Need 500 credits.")
    data[uid]['credits'] -= 500; data[uid]['booster_end'] = time.time() + 86400
    save_user_data(data); await ctx.send("🚀 Booster Activated! 2x rewards for 24h.")

# --- 5. START BOT ---
if TOKEN: 
    bot.run(TOKEN)
else:
    print("TOKEN NOT FOUND! Check your .env file.")