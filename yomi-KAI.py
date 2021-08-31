import discord
from discord.ext import commands
import datetime
import time
import os
from voicetext import VoiceText
import wave
import asyncio
from collections import defaultdict, deque
import tokens
import re
import json

prefix = "yomi."

intents = discord.Intents.default()
intents.members = True 
bot = commands.Bot(command_prefix=prefix, intents=intents)

vt = VoiceText(tokens.VOICETEXT_API_KEY)
check_text_channel = None


def mention(Text):
    P = "<@!?([0-9]+)>" #パターン
    return re.findall(P,Text) #メンションからユーザーIDの抜き出し


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


#接続時の処理
@bot.event
async def on_ready():
    dt_now = datetime.datetime.now()
    print(f"[{dt_now}][INFO]Launch complete! Logged in as {bot.user.name}.")

#もとからあるhelpコマンドを無効化
bot.remove_command('help')

embed = discord.Embed(title="yomi-KAI", description="テキスト読み上げbotです。", inline="false")
embed.add_field(name=f"{prefix}c", value="発言者と同じボイスチャンネルに接続します。", inline="false")
embed.add_field(name=f"{prefix}dc", value="ボイスチャンネルから切断します。", inline="false")
embed.add_field(name=f"{prefix}help", value="このヘルプを表示します。", inline="false")

#ヘルプコマンド
@bot.command()
async def help(ctx):
    await ctx.send(embed=embed)

#ボイスチャンネルに接続
@bot.command()
async def c(ctx):
    if ctx.author.voice is None:
        await ctx.channel.send(f"{ctx.author.mention}さんはボイスチャンネルに接続していません")
        dt_now = datetime.datetime.now()
        print(f"[{dt_now}][INFO]{ctx.author}さんはボイスチャンネルに接続していません")          
    
    else:
        await ctx.author.voice.channel.connect()
        global check_text_channel
        check_text_channel = ctx.channel
        await ctx.channel.send(f"{ctx.author.voice.channel.name}に接続しました")
        dt_now = datetime.datetime.now()
        print(f"[{dt_now}][INFO]{ctx.author.voice.channel.name}に接続しました")

#ボイスチャンネルから切断
@bot.command()
async def dc(ctx):
    if ctx.guild.voice_client is None:
        await ctx.channel.send("ボイスチャンネルに接続していません")
        dt_now = datetime.datetime.now()
        print(f"[{dt_now}][INFO]ボイスチャンネルに接続していません")

    else:
        await ctx.guild.voice_client.disconnect()
        await ctx.channel.send("切断しました")
        dt_now = datetime.datetime.now()
        print(f"[{dt_now}][INFO]切断しました")

@bot.command()
async def dict(ctx, arg1, arg2, arg3):
    if arg1 == "add":
        if os.path.isfile(f"./dict/{ctx.guild.id}.json") == True:
            with open(f"./dict/{ctx.guild.id}.json", "r", encoding="UTF-8")as f:
                word = json.load(f)
        else:
            word = {}
        
        word[arg2] = arg3
        with open(f"./dict/{ctx.guild.id}.json", "w", encoding="UTF-8")as f:
            f.write(json.dumps(word, indent=2, ensure_ascii=False))


#メッセージが送られた時
@bot.event
async def on_message(message):
    #コマンドをコマンドとしてトリガーし、読み上げから除外
    if message.content.startswith(prefix):
        await bot.process_commands(message)
        return

    #botの発言は無視
    if message.author.bot:
        return

    #読み上げ
    elif message.channel == check_text_channel:
        if message.guild.voice_client is not None:

            #文字置換
            #URL置換
            read_msg = re.sub(r"https?://.*", "URL", message.content)

            #メンション置換
            if "<@" and ">" in message.content: #メンションがあった場合実行
                Temp = mention(message.content)
                for i in range(len(Temp)): #返り値(リスト型)の回数ループ
                    Temp[i] = int(Temp[i]) #返り値のデータをstrからintに変換
                for i in range(len(Temp)): #返り値(リスト型)の(ry
                    user = re.sub(r"#\d{4}", "", str(bot.get_user(Temp[i]))) #ユーザー情報取得
                    read_msg = "アット" + re.sub("<@!?[0-9]+>", user, read_msg) 
            
            #音声ファイル作成
            ut = time.time()
            with open(f"./temp/{ut}.wav","wb") as f:
                f.write(vt.speed(120).to_wave(read_msg))

            #音声読み上げ
            enqueue(message.guild.voice_client, message.guild, discord.FFmpegPCMAudio(f"./temp/{ut}.wav"))
            dt_now = datetime.datetime.now()
            print(f"[{dt_now}][INFO]ReadSentence:{read_msg}")

            #音声ファイル削除
            with wave.open(f"./temp/{ut}.wav", "rb")as f:
                wave_length=(f.getnframes() / f.getframerate()) #再生時間
                
            dt_now = datetime.datetime.now()
            print(f"[{dt_now}][INFO]PlayTime:{wave_length}")
            await asyncio.sleep(wave_length + 5)
            os.remove(f"./temp/{ut}.wav")


bot.run(tokens.DISCORD_TOKEN)