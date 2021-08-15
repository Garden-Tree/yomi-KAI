import discord
import datetime
import time
import os
from voicetext import VoiceText
import wave
import asyncio
from collections import defaultdict, deque
import tokens


client = discord.Client()
vt = VoiceText(tokens.VOICETEXT_TOKEN)
check_text_channel = None


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
@client.event
async def on_ready():
    dt_now = datetime.datetime.now()
    print(f"[{dt_now}]Launch complete!")

#メッセージが送られた時
@client.event
async def on_message(message):
    
    #botの発言は無視
    if message.author.bot:
        return
    
    #ボイスチャンネルに接続
    elif message.content == "yomi.c":
        if message.author.voice is None:
            await message.channel.send(f"{message.author.mention}さんはボイスチャンネルに接続していません")
            dt_now = datetime.datetime.now()
            print(f"[{dt_now}]{message.author}さんはボイスチャンネルに接続していません")          
        
        else:
            await message.author.voice.channel.connect()
            global check_text_channel
            check_text_channel = message.channel
            await message.channel.send(f"{message.author.voice.channel.name}に接続しました")
            dt_now = datetime.datetime.now()
            print(f"[{dt_now}]{message.author.voice.channel.name}に接続しました")
            
    
    #ボイスチャンネルから切断
    elif message.content == "yomi.dc":
        if message.guild.voice_client is None:
            await message.channel.send("ボイスチャンネルに接続していません")
            dt_now = datetime.datetime.now()
            print(f"[{dt_now}]ボイスチャンネルに接続していません")

        else:
            await message.guild.voice_client.disconnect()
            await message.channel.send("切断しました")
            dt_now = datetime.datetime.now()
            print(f"[{dt_now}]切断しました")
            
    #読み上げ
    elif message.channel == check_text_channel:
        if message.guild.voice_client is not None:
            #音声ファイル作成
            ut = time.time()
            with open(f"{ut}.wav","wb") as f:
                f.write(vt.speed(120).to_wave(message.content))

            enqueue(message.guild.voice_client, message.guild, discord.FFmpegPCMAudio(f"{ut}.wav"))
        
            #音声読み上げ
            #message.guild.voice_client.play(discord.FFmpegPCMAudio(f"{ut}.wav"))
            

            #音声ファイル削除
            with wave.open(f"{ut}.wav", "rb")as f:
                wave_length=(f.getnframes() / f.getframerate()) #再生時間
            print(f"PlayTime:{wave_length}")
            await asyncio.sleep(wave_length + 10)
            os.remove(f"{ut}.wav")

client.run(tokens.DISCORD_TOKEN)