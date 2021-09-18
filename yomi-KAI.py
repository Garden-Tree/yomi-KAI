import discord
from discord.ext import commands
from datetime import datetime
import time
import os
from voicetext import VoiceText
import wave
import asyncio
from collections import defaultdict, deque
import re
import json
import pprint
import glob
from logging import StreamHandler, FileHandler, Formatter, basicConfig, getLogger, INFO, DEBUG, NOTSET

# ストリームハンドラの設定
sh = StreamHandler()
sh.setLevel(INFO)
sh.setFormatter(Formatter("[%(asctime)s] %(name)s:%(lineno)s %(funcName)s [%(levelname)s]: %(message)s"))

# 保存先の有無チェック
if not os.path.isdir('./log'):
    os.makedirs('./log', exist_ok=True)

fh = FileHandler(f"./log/{datetime.now():%Y-%m-%d_%H%M%S}.log", encoding="utf-8")
fh.setLevel(DEBUG)
fh.setFormatter(Formatter("[%(asctime)s] %(name)s:%(lineno)s %(funcName)s [%(levelname)s]: %(message)s"))

# ルートロガーの設定
basicConfig(level=NOTSET, handlers=[sh, fh])
logger = getLogger(__name__)

with open("settings.json", "r") as f:
    settings = json.load(f)
    DISCORD_TOKEN = settings["DISCORD_TOKEN"]
    VOICETEXT_API_KEY = settings["VOICETEXT_API_KEY"] + ":"
    PREFIX = settings["PREFIX"]

intents = discord.Intents.default()
intents.members = True 
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

vt = VoiceText(VOICETEXT_API_KEY)

check_text_channel = None

#キュー
queue_dict = defaultdict(deque)

def enqueue(voice_client, guild, source):
    queue = queue_dict[guild.id]
    queue.append(source)
    if not voice_client.is_playing():
        play(voice_client, queue)

def play(voice_client, queue):
    if not queue or voice_client.is_playing():
        return
    source = queue.popleft()
    voice_client.play(source, after=lambda e:play(voice_client, queue))

#起動時に./temp内の*.wavをすべて削除
for filename in glob.glob("./temp/*.wav"):
    os.remove(filename)

#ログを10個残す
file_list = glob.glob("./log/*.log")
if len(file_list) > 10:
    for i in range(len(file_list)-10):
        os.remove(file_list[i])

#接続時の処理
@bot.event
async def on_ready():
    logger.info(f"Launch complete! Logged in as {bot.user.name}.")

#もとからあるhelpコマンドを無効化
bot.remove_command('help')

#ヘルプコマンド
@bot.command()
async def help(ctx):
    embed = discord.Embed(title="yomi-KAI", description="テキスト読み上げbotです。", inline="false")
    embed.add_field(name=f"{PREFIX}c", value="発言者と同じボイスチャンネルに接続します。", inline="false")
    embed.add_field(name=f"{PREFIX}dc", value="ボイスチャンネルから切断します。", inline="false")
    embed.add_field(name=f"{PREFIX}dict", value=f"辞書に関する操作です。詳しくは`{PREFIX}dict help`を参照してください。", inline="false")
    embed.add_field(name=f"{PREFIX}help", value="このヘルプを表示します。", inline="false")
    await ctx.send(embed=embed)
    logger.info("helpを表示")

#ボイスチャンネルに接続
@bot.command()
async def c(ctx):
    if ctx.author.voice is None:
        await ctx.channel.send(f"{ctx.author.mention}さんはボイスチャンネルに接続していません")
        logger.info(f"{ctx.author}さんはボイスチャンネルに接続していません")
        return

    global check_text_channel

    if ctx.guild.voice_client is not None:
        await ctx.guild.voice_client.move_to(ctx.author.voice.channel)
        check_text_channel = ctx.channel
        await ctx.channel.send(f"{ctx.author.voice.channel.name}に接続しました")
        logger.info(f"{ctx.author.voice.channel.name}に接続しました")
        return

    await ctx.author.voice.channel.connect()
    check_text_channel = ctx.channel
    await ctx.channel.send(f"{ctx.author.voice.channel.name}に接続しました")
    logger.info(f"{ctx.author.voice.channel.name}に接続しました")

#ボイスチャンネルから切断
@bot.command()
async def dc(ctx):
    if ctx.guild.voice_client is None:
        await ctx.channel.send("ボイスチャンネルに接続していません")
        logger.info(f"ボイスチャンネルに接続していません")
        return

    await ctx.guild.voice_client.disconnect()
    await ctx.channel.send("切断しました")
    logger.info(f"切断しました")

#辞書
@bot.command()
async def dict(ctx, *args):
    if os.path.isfile(f"./dict/{ctx.guild.id}.json") == True:
        with open(f"./dict/{ctx.guild.id}.json", "r", encoding="UTF-8")as f:
            word = json.load(f)
    else:
        word = {}

    if args[0] == "add" and len(args) == 3:
        word[args[1]] = args[2]
        with open(f"./dict/{ctx.guild.id}.json", "w", encoding="UTF-8")as f:
            f.write(json.dumps(word, indent=2, ensure_ascii=False))
        await ctx.channel.send(f"辞書に`{args[1]}`を`{args[2]}`として登録しました")
        logger.info(f"辞書に{args[1]}を{args[2]}として登録しました")
        return

    if args[0] == "del" and len(args) == 2:
        del word[args[1]]
        with open(f"./dict/{ctx.guild.id}.json", "w", encoding="UTF-8")as f:
            f.write(json.dumps(word, indent=2, ensure_ascii=False))
        await ctx.channel.send(f"辞書から`{args[1]}`を削除しました")
        logger.info(f"辞書から{args[1]}を削除しました")
        return

    if args[0] == "list" and len(args) == 1:
        await ctx.channel.send("辞書を表示します")
        logger.info(f"辞書を表示します")
        await ctx.channel.send(pprint.pformat(word, depth=1))
        return

    if args[0] == "help" and len(args) == 1:
        embed = discord.Embed(title="辞書機能ヘルプ", description="辞書機能のヘルプです。", inline="false")
        embed.add_field(name=f"{PREFIX}dict add `word` `yomi`", value="`word`を`yomi`と読むように辞書に追加します。", inline="false")
        embed.add_field(name=f"{PREFIX}dict del `word`", value="`word`を辞書から削除します。", inline="false")
        embed.add_field(name=f"{PREFIX}dict list", value="現在登録されている辞書を表示します。", inline="false")
        embed.add_field(name=f"{PREFIX}dict help", value="このヘルプを表示します。", inline="false")
        await ctx.send(embed=embed)
        logger.info("dict.helpを表示")
        return

    else:
        await ctx.channel.send(f"コマンドが間違っています。`{PREFIX}dict help`を参照してください")
        logger.info(f"コマンドが間違っています。`{PREFIX}dict help`を参照してください")

#メッセージが送られた時
@bot.event
async def on_message(message):
    #コマンドをコマンドとしてトリガーし、読み上げから除外
    if message.content.startswith(PREFIX):
        await bot.process_commands(message)
        return

    #botの発言は無視
    if message.author.bot:
        return

    #読み上げ
    if message.channel == check_text_channel and message.guild.voice_client is not None:
        #URL置換
        read_msg = re.sub(r"https?://.*", "URL", message.content)

        #メンション置換
        if "<@" and ">" in message.content:
            P = "<@!?([0-9]+)>" #パターン
            Temp = re.findall(P, message.content)
            for i in range(len(Temp)):
                Temp[i] = int(Temp[i])
                user = message.guild.get_member(Temp[i])
                read_msg = re.sub(f"<@!?{Temp[i]}>", "アット" + user.display_name, read_msg)

        #辞書置換
        if os.path.isfile(f"./dict/{message.guild.id}.json") == True:
            with open(f"./dict/{message.guild.id}.json", "r", encoding="UTF-8")as f:
                word = json.load(f)

            read_list = [] # あとでまとめて変換するときの読み仮名リスト
            for i, one_dic in enumerate(word.items()): # one_dicは単語と読みのタプル。添字はそれぞれ0と1。
                read_msg = read_msg.replace(one_dic[0], '{'+str(i)+'}')
                read_list.append(one_dic[1]) # 変換が発生した順に読みがなリストに追加
            read_msg = read_msg.format(*read_list) #読み仮名リストを引数にとる

        #音声ファイル作成
        gen_time = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        with open(f"./temp/{gen_time}.wav","wb") as f:
            f.write(vt.speed(120).to_wave(read_msg))

        #音声読み上げ
        enqueue(message.guild.voice_client, message.guild, discord.FFmpegPCMAudio(f"./temp/{gen_time}.wav"))
        logger.info(f"ReadSentence:{read_msg}")

        #音声ファイル削除
        with wave.open(f"./temp/{gen_time}.wav", "rb")as f:
            wave_length=(f.getnframes() / f.getframerate()) #再生時間 
        logger.info(f"PlayTime:{wave_length}")
        await asyncio.sleep(wave_length + 5)

        os.remove(f"./temp/{gen_time}.wav")

#誰も居なくなると自動切断
@bot.event
async def on_voice_state_update(member, before, after):
    if (member.guild.voice_client is not None and after.channel is None and member.id != bot.user.id and member.guild.voice_client.channel is before.channel and len(member.guild.voice_client.channel.members) == 1):
        await member.guild.voice_client.disconnect()
        await check_text_channel.send("自動切断しました")
        logger.info(f"自動切断しました")

bot.run(DISCORD_TOKEN)