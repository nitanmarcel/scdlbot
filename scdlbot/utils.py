import logging
import os

import pkg_resources
import requests
import pyshorteners
import untangle

try:
    import youtube_dl
    youtube_dl_bin_name = 'youtube-dl'
except:
    import youtube_dlc as youtube_dl
    youtube_dl_bin_name = 'youtube-dlc'

from boltons.urlutils import URL
from urllib.parse import urlparse
from plumbum import local, ProcessExecutionError, ProcessTimedOut
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from scdlbot.exceptions import *

# from requests.exceptions import Timeout, RequestException, SSLError

bin_path = os.getenv('BIN_PATH', '')
scdl_bin = local[os.path.join(bin_path, 'scdl')]
bandcamp_dl_bin = local[os.path.join(bin_path, 'bandcamp-dl')]
youtube_dl_bin = local[os.path.join(bin_path, youtube_dl_bin_name)]

BOTAN_TRACK_URL = 'https://api.botan.io/track'

logger = logging.getLogger(__name__)


def get_response_text(file_name):
    # https://stackoverflow.com/a/20885799/2490759
    path = '/'.join(('texts', file_name))
    return pkg_resources.resource_string(__name__, path).decode("UTF-8")


def get_direct_urls(url, cookies_file=None, cookies_download_file=None, source_ip=None, proxy=None):
    logger.debug("Entered get_direct_urls")
    youtube_dl_args = []

    # https://github.com/ytdl-org/youtube-dl#how-do-i-pass-cookies-to-youtube-dl
    if cookies_file:
        if "http" in cookies_file:
            try:
                r = requests.get(cookies_file, allow_redirects=True, timeout=5)
                open(cookies_download_file, 'wb').write(r.content)
                youtube_dl_args.extend(["--cookies", cookies_download_file])
            except:
                pass
        else:
            youtube_dl_args.extend(["--cookies", cookies_file])

    if source_ip:
        youtube_dl_args.extend(["--source-address", source_ip])

    if proxy:
        youtube_dl_args.extend(["--proxy", proxy])

    youtube_dl_args.extend(["--get-url", url])
    try:
        ret_code, std_out, std_err = youtube_dl_bin[youtube_dl_args].run(timeout=60)
    except ProcessTimedOut as exc:
        raise URLTimeoutError
    except ProcessExecutionError as exc:
        # TODO: look at case: one page has multiple videos, some available, some not
        if "returning it as such" in exc.stderr:
            raise URLDirectError
        if "proxy server" in exc.stderr:
            raise URLCountryError
        raise exc
    if "yt_live_broadcast" in std_out:
        raise URLLiveError
    return std_out


def get_italic(text):
    return "_{}_".format(text)


def youtube_dl_func(url, ydl_opts, queue=None):
    ydl = youtube_dl.YoutubeDL(ydl_opts)
    try:
        ydl.download([url])
    except Exception as exc:
        ydl_status = 1, str(exc)
        # ydl_status = exc  #TODO: pass and re-raise original Exception
    else:
        ydl_status = 0, "OK"
    if queue:
        queue.put(ydl_status)
    else:
        return ydl_status


# def botan_track(token, message, event_name):
#     try:
#         # uid = message.chat_id
#         uid = message.from_user.id
#     except AttributeError:
#         logger.warning('Botan no chat_id in message')
#         return False
#     num_retries = 2
#     ssl_verify = True
#     for i in range(num_retries):
#         try:
#             r = requests.post(
#                 BOTAN_TRACK_URL,
#                 params={"token": token, "uid": uid, "name": event_name},
#                 data=message.to_json(),
#                 verify=ssl_verify,
#                 timeout=2,
#             )
#             return r.json()
#         except Timeout:
#             logger.exception("Botan timeout on event: %s", event_name)
#         except SSLError:
#             ssl_verify = False
#         except (Exception, RequestException, ValueError):
#             # catastrophic error
#             logger.exception("Botan 🙀astrophic error on event: %s", event_name)
#     return False

def shorten_url(url):
    s = pyshorteners.Shortener(timeout=10)
    shorteners = [s.chilpit, s.osdb, s.isgd, s.dagd, s.clckru]
    for sortener in shorteners:
        try:
            return sortener.short(url)
        except Exception:
            pass

    return url

def get_netloc(url):
    try:
        parse = urlparse(url)
        if all([parse.netloc, parse.scheme]):
            netloc = parse.netloc
            if netloc.startswith('www'):
                netloc = netloc.split(".", 1)[-1]
            return netloc
    except Exception:
        return None
    return None

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'k', 2: 'm', 3: 'g', 4: 't'}
    while size > power:
        size /= power
        n += 1
    return size + power_labels[n]+'b'


def guesss_link_type(url):
    valid_audio = ["audio", "mp3", "m4a", "wav", "flac"]
    valid_video = ['video', 'mp4', 'avi', 'webm']
    is_audio = bool([t for t in valid_audio if t in url.lower()])
    is_video = bool([t for t in valid_video if t in url.lower()])
    return  "Audio" if is_audio else "Video" if is_video else "Unknown"

def get_link_type(url):
    resp = requests.head(url)
    res = ""
    if resp:
        content_type = resp.headers.get('Content-Type')
        if content_type:
            birate = resp.headers.get('x-amz-meta-bitrate')
            content_size = resp.headers.get('Content-Length')
            if content_size: 
                res += format_bytes(int(content_size)) + " | " # x kb |
            if birate:
                res += str(birate) + "kbs | "
            res += content_type.split("/")[0].capitalize()
    return res or guesss_link_type(url)


def log_and_track(event_name, message=None):
    logger.info("Event: %s", event_name)
    if message:
        pass
        # if self.botan_token:
        #     return botan_track(self.botan_token, message, event_name)

# 

def get_link_buttons(urls):
    buttons = []
    max_buttons = 100
    for url in urls:
        source = ".".join(urlparse(url).netloc.split(".")[-2:])
        direct_urls = urls[url].splitlines()
        for direct_url in direct_urls:
            netloc = get_netloc(direct_url)
            if netloc:
                if netloc.split(".")[0] == "manifest":
                    logger.debug("Parsing manifest file")
                    resp = requests.get(direct_url)
                    if resp:
                        content = resp.content
                        if content:
                            obj = untangle.parse(content.decode())
                            for ads in obj.MPD.Period.AdaptationSet:
                                for rep in ads.Representation:
                                    direct_url = rep.BaseURL.cdata
                                    netloc = get_netloc(direct_url)
                                    content_type = get_link_type(direct_url)
                                    logger.debug("Got conent type: " + str(content_type))
                                    if content_type.split()[-1] in ["Video", "Audio", "Unknown"]:
                                        if len(buttons) < max_buttons:
                                            buttons.append(InlineKeyboardButton(text=content_type + " | " + source, url=shorten_url(direct_url)))
                else:
                    content_type = get_link_type(direct_url)
                    logger.debug("Got content type: " + str(content_type.split()[-1]))
                    if content_type.split()[-1] in ["Video", "Audio", "Unknown"]:
                        if len(buttons) < max_buttons:
                            buttons.append(InlineKeyboardButton(text=content_type + " | " + source, url=shorten_url(direct_url)))
        pairs = list(zip(buttons[::2], buttons[1::2]))
        if len(buttons) % 2 == 1:
            pairs.append(buttons[-1])
        return InlineKeyboardMarkup(pairs if len(pairs) > 1 else [pairs,])