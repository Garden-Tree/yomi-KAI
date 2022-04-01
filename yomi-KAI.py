import asyncio
import glob
import json
import os
import pprint
import re
import sys
import wave
import time
from collections import defaultdict, deque
from datetime import datetime
from logging import (DEBUG, INFO, NOTSET, FileHandler, Formatter,
                     StreamHandler, basicConfig, getLogger)

import discord
from discord.ext import commands
from voicetext import VoiceText

# ディレクトリ作成
if not os.path.isdir("dict"):
    os.mkdir("dict")
if not os.path.isdir("log"):
    os.mkdir("log")
if not os.path.isdir("temp"):
    os.mkdir("temp")

# ログの設定
format = "[%(asctime)s] %(name)s:%(lineno)s %(funcName)s [%(levelname)s]: %(message)s"

sh = StreamHandler()
sh.setLevel(INFO)
sh.setFormatter(Formatter(format))

fh = FileHandler(f"./log/{datetime.now():%Y-%m-%d_%H%M%S}.log", encoding="UTF-8")
fh.setLevel(DEBUG)
fh.setFormatter(Formatter(format))

basicConfig(level=NOTSET, handlers=[sh, fh])
logger = getLogger(__name__)

# 起動時に./temp内の*.wavをすべて削除
for filename in glob.glob("./temp/*.wav"):
    os.remove(filename)

# ログを10個残す
file_list = glob.glob("./log/*.log")
if len(file_list) > 10:
    for i in range(len(file_list)-10):
        os.remove(file_list[i])

# setting.jsonの確認
try:
    with open("settings.json", "r", encoding="UTF-8") as f:
        content = f.read()
        re_content = re.sub(r"/\*[\s\S]*?\*/|//.*", "", content) # コメントを正規表現で削除
        settings = json.loads(re_content)
        DISCORD_TOKEN = settings["DISCORD_TOKEN"]
        VOICETEXT_API_KEY = settings["VOICETEXT_API_KEY"] + ":"
        PREFIX = settings["PREFIX"]
        SPEAKER = settings["SPEAKER"]
        PITCH = settings["PITCH"]
        SPEED = settings["SPEED"]
except:
    logger.exception("settings.jsonが見つかりません。")
    sys.exit()

# VOICECETEXT_API_KEYの確認
try:
    vt = VoiceText(VOICETEXT_API_KEY)
except:
    logger.exception("VOICETEXT_API_KEYが不適切です。")
    sys.exit()

# キュー
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

intents = discord.Intents.default()
intents.members = True 
bot = commands.Bot(command_prefix=PREFIX, intents=intents)


connected_channel = {}

# 接続時の処理
@bot.event
async def on_ready():
    logger.info(f"Launch complete! Logged in as [{bot.user.name}]. PREFIX is [{PREFIX}].")

# もとからあるhelpコマンドを無効化
bot.remove_command('help')

# ヘルプコマンド
@bot.command()
async def help(ctx):
    help_embed = discord.Embed(title="yomi-KAI", inline="false", color=0x3399cc)
    help_embed.add_field(name=f"{PREFIX}c", value="発言者と同じボイスチャンネルに接続します。", inline="false")
    help_embed.add_field(name=f"{PREFIX}dc", value="ボイスチャンネルから切断します。", inline="false")
    help_embed.add_field(name=f"{PREFIX}dict", value=f"辞書に関する操作です。詳しくは`{PREFIX}dict help`を参照してください。", inline="false")
    help_embed.add_field(name=f"{PREFIX}help", value="このヘルプを表示します。", inline="false")
    await ctx.send(embed=help_embed)
    logger.info("helpを表示")

# ボイスチャンネルに接続
@bot.command()
async def c(ctx):
    if ctx.author.voice is None:
        await ctx.channel.send(f"{ctx.author.mention}さんはボイスチャンネルに接続していません")
        logger.info(f"{ctx.author}さんはボイスチャンネルに接続していません")
        return

    global connected_channel

    async def connect(ctx):
        connected_embed = discord.Embed(title="読み上げ開始", inline="false", color=0x3399cc)
        connected_embed.add_field(name="テキストチャンネル", value=f"{ctx.channel.name}", inline="false")
        connected_embed.add_field(name="ボイスチャンネル", value=f"{ctx.author.voice.channel.name}", inline="false")
        await ctx.send(embed=connected_embed)
        logger.info(f"{ctx.author.voice.channel.name}に接続しました")
        connected_channel[ctx.guild] = ctx.channel

    if ctx.guild.voice_client is not None:
        await ctx.guild.voice_client.move_to(ctx.author.voice.channel)
        await connect(ctx)
        return

    await ctx.author.voice.channel.connect()
    await connect(ctx)

# ボイスチャンネルから切断
@bot.command()
async def dc(ctx):
    if ctx.guild.voice_client is None:
        await ctx.channel.send("ボイスチャンネルに接続していません")
        logger.info(f"ボイスチャンネルに接続していません")
        return

    await ctx.guild.voice_client.disconnect()
    await ctx.channel.send("切断しました")
    logger.info(f"切断しました")
    connected_channel.pop(ctx.guild)

# 辞書
@bot.command()
async def dict(ctx, *args):
    if os.path.isfile(f"./dict/{ctx.guild.id}.json") == True:
        with open(f"./dict/{ctx.guild.id}.json", "r", encoding="UTF-8")as f:
            word = json.load(f)
    else:
        word = {}

    if len(args) > 0:

        if args[0] == "add" and len(args) == 3:
            word[args[1]] = args[2]
            with open(f"./dict/{ctx.guild.id}.json", "w", encoding="UTF-8")as f:
                f.write(json.dumps(word, indent=2, ensure_ascii=False))
            dict_add_embed = discord.Embed(title="辞書追加", inline="true", color=0x3399cc)
            dict_add_embed.add_field(name="単語", value=f"{args[1]}", inline="false")
            dict_add_embed.add_field(name="読み", value=f"{args[2]}", inline="false")
            await ctx.send(embed=dict_add_embed)
            logger.info(f"辞書に{args[1]}を{args[2]}として追加しました")
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
            await ctx.channel.send("```" + pprint.pformat(word, depth=1) + "```")
            return

        if args[0] == "help" and len(args) == 1:
            dict_help_embed = discord.Embed(title="辞書機能ヘルプ", inline="false", color=0x3399cc)
            dict_help_embed.add_field(name=f"{PREFIX}dict add `word` `yomi`", value="`word`を`yomi`と読むように辞書に追加します。", inline="false")
            dict_help_embed.add_field(name=f"{PREFIX}dict del `word`", value="`word`を辞書から削除します。", inline="false")
            dict_help_embed.add_field(name=f"{PREFIX}dict list", value="現在登録されている辞書を表示します。", inline="false")
            dict_help_embed.add_field(name=f"{PREFIX}dict help", value="このヘルプを表示します。", inline="false")
            await ctx.send(embed=dict_help_embed)
            logger.info("dict.helpを表示")
            return

        else:
            await ctx.channel.send(f"コマンドが間違っています。`{PREFIX}dict help`を参照してください")
            logger.info(f"コマンドが間違っています。`{PREFIX}dict help`を参照してください")

    else:
        await ctx.channel.send(f"コマンドが間違っています。`{PREFIX}dict help`を参照してください")
        logger.info(f"コマンドが間違っています。`{PREFIX}dict help`を参照してください")

# メッセージが送られた時
@bot.event
async def on_message(message):
    # コマンドをコマンドとしてトリガーし、読み上げから除外
    if message.content.startswith(PREFIX):
        await bot.process_commands(message)
        return

    # botの発言は無視
    if message.author.bot:
        return

    # 読み上げ
    if message.channel in connected_channel.values() and message.guild.voice_client is not None:
        read_msg = message.content

        # debug
        #print(read_msg)

        # 辞書置換
        if os.path.isfile(f"./dict/{message.guild.id}.json") == True:
            with open(f"./dict/{message.guild.id}.json", "r", encoding="UTF-8")as f:
                word = json.load(f)
            read_list = [] # あとでまとめて変換するときの読み仮名リスト
            for i, one_dic in enumerate(word.items()): # one_dicは単語と読みのタプル。添字はそれぞれ0と1。
                read_msg = read_msg.replace(one_dic[0], '{'+str(i)+'}')
                read_list.append(one_dic[1]) # 変換が発生した順に読みがなリストに追加
            read_msg = read_msg.format(*read_list) # 読み仮名リストを引数にとる

        # URL置換
        read_msg = re.sub(r"https?://.*?\s|https?://.*?$", "URL", read_msg)

        # ネタバレ置換
        read_msg = re.sub(r"\|\|.*?\|\|", "ネタバレ", read_msg)

        # メンション置換
        if "<@" and ">" in message.content:
            Temp = re.findall("<@!?([0-9]+)>", message.content)
            for i in range(len(Temp)):
                Temp[i] = int(Temp[i])
                user = message.guild.get_member(Temp[i])
                read_msg = re.sub(f"<@!?{Temp[i]}>", "アット" + user.display_name, read_msg)

        # サーバー絵文字置換
        read_msg = re.sub(r"<:(.*?):[0-9]{18}>", r"\1", read_msg)

        # 音声ファイル作成
        gen_time = time.time()
        with open(f"./temp/{gen_time}.wav","wb") as f:
            f.write(vt.speaker(SPEAKER).pitch(PITCH).to_wave(read_msg))

        # 音声読み上げ
        enqueue(message.guild.voice_client, message.guild, discord.FFmpegPCMAudio(f"./temp/{gen_time}.wav", options= "-af atempo=" + str(SPEED / 100)))
        logger.info(f"ReadSentence:{read_msg}")

        # 音声ファイル削除
        with wave.open(f"./temp/{gen_time}.wav", "rb")as f:
            wave_length=(f.getnframes() / f.getframerate() / (SPEED / 100)) # 再生時間 
        logger.info(f"PlayTime:{wave_length}")
        await asyncio.sleep(wave_length + 30)

        os.remove(f"./temp/{gen_time}.wav")

# 誰も居なくなると自動切断
@bot.event
async def on_voice_state_update(member, before, after):
    if (member.guild.voice_client is not None and member.id != bot.user.id and member.guild.voice_client.channel is before.channel and len(member.guild.voice_client.channel.members) == 1):
        await member.guild.voice_client.disconnect()
        await connected_channel[member.guild].send("自動切断しました")
        logger.info(f"自動切断しました")
        connected_channel.pop(member.guild)

try:
    bot.run(DISCORD_TOKEN)
except:
    logger.exception("DISCORD_TOKENが不適切です。")
    sys.exit()
