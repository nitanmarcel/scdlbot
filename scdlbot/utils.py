import logging
import os

import pkg_resources
import requests
import untangle
import pyshorteners

try:
    import youtube_dl
    youtube_dl_bin_name = 'youtube-dl'
except:
    import youtube_dlc as youtube_dl
    youtube_dl_bin_name = 'youtube-dlc'

from boltons.urlutils import URL
from plumbum import local, ProcessExecutionError, ProcessTimedOut

from scdlbot.exceptions import *

from urllib.parse import urlparse, parse_qs
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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
    if not url.startswith("http"):
        url = "http://"
    try:
        return pyshorteners.Shortener(timeout=20).chilpit.short(url)
    except:
        return url


def log_and_track(event_name, message=None):
    logger.info("Event: %s", event_name)
    if message:
        pass
        # if self.botan_token:
        #     return botan_track(self.botan_token, message, event_name)

def guess_link_type(url): # TODO find multiple formats: mp3, wav, etc
    if "audio" in url:
        return "Audio"
    if "mp3" in url:
        return "Audio"
    if "video" in url:
        return "Video"
    if "mp4" in url:
        return "Audio"
    return "Unknown"
def get_link_type(url):
    r = requests.head('url')
    content_type = r.headers.get('Content-Type')
    if content_type:
        return content_type.split("/")[0].capitalize()
    else:
        return guess_link_type


def get_link_buttons(urls):
    link_buttons = []
    max_link_buttons = 99 # 100 - 1
    for url in urls:
        link_source = ".".join(urlparse(url).netloc.split(".")[-2:])
        direct_urls = urls[url].splitlines()
        for direct_url in direct_urls:
            content_type = "Unknown"
            if "http" in direct_url:
                parsed_url = urlparse(direct_url)
                netloc = parsed_url.netloc
                if netloc.startswith("www."):
                    netloc = ".".join(netloc.split(".", 1)[-1])
                if netloc.split('.')[0] == "manifest":
                    r = requests.get(direct_url, allow_redirects=True)
                    obj = untangle.parse(r.content.decode())
                    if obj:
                        for ads in obj.MPD.Period.AdaptationSet:
                            for rep in ads.Representation:
                                direct_url = rep.BaseURL.cdata
                                parsed_url = urlparse(direct_url)
                                netloc = parsed_url.netloc
                                if netloc.startswith("www."):
                                    netloc = ".".join(netloc.split(".", 1)[-1])
                                content_type = get_link_type(direct_url)
                                if len(link_buttons) < max_link_buttons:
                                    link_buttons.append(InlineKeyboardButton(text=content_type + " | " + link_source, url=shorten_url(direct_url)),)
                else:
                    content_type = get_link_type(direct_url)
                    if len(link_buttons) < max_link_buttons:
                        link_buttons.append(InlineKeyboardButton(text=content_type + " | " + link_source, url=shorten_url(direct_url)),)
    if link_buttons:
        if len(link_buttons) > 1:
            pairs = list(zip(link_buttons[::2], link_buttons[1::2]))
            if len(link_buttons) % 2 == 1:
                pairs.append(link_buttons[-1])
        else:
            pairs = link_buttons
        return InlineKeyboardMarkup([pairs,]) # Remainder to add it in a list with comma if pairs has just one element
    else:
        return []