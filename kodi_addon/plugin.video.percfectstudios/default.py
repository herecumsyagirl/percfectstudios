import sys
import urllib.parse
import urllib.request
import json

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

ADDON         = xbmcaddon.Addon()
ADDON_URL     = sys.argv[0]
ADDON_HANDLE  = int(sys.argv[1])
ARGS          = urllib.parse.parse_qs(urllib.parse.urlparse(sys.argv[2]).query)

API_URL = (ADDON.getSetting('api_url') or 'https://percfectstudios.onrender.com').rstrip('/')
API_KEY = ADDON.getSetting('api_key')


def build_url(params):
    return '{}?{}'.format(ADDON_URL, urllib.parse.urlencode(params))


def api(method, path, body=None):
    url = API_URL + path
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    req.add_header('X-API-Key', API_KEY)
    with urllib.request.urlopen(req, timeout=150) as r:
        return json.loads(r.read().decode())


def main_menu():
    entries = [
        ('🖼   Generate Image',  'generate_image', True),
        ('🎬   Generate Video',  'generate_video', True),
        ('📁   My Gallery',      'gallery',         True),
        ('⚙️   Settings',         'settings',        False),
    ]
    for label, action, is_folder in entries:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, build_url({'action': action}), li, isFolder=is_folder)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def prompt_text(heading):
    kb = xbmc.Keyboard('', heading)
    kb.doModal()
    return kb.getText() if kb.isConfirmed() else None


def generate_image():
    if not API_KEY:
        xbmcgui.Dialog().ok('Setup required', 'Open Settings and enter your API key from percfectai.com')
        ADDON.openSettings()
        return

    prompt = prompt_text('Describe the image you want to create')
    if not prompt:
        return

    progress = xbmcgui.DialogProgress()
    progress.create('PercfectStudios', 'Generating image…')
    try:
        result = api('POST', '/api/generate/image', {'prompt': prompt})
        progress.close()
        url = result.get('url')
        if url:
            li = xbmcgui.ListItem(prompt)
            li.setArt({'thumb': url, 'poster': url})
            xbmc.Player().play(url, li)
            xbmcgui.Dialog().ok('Done', 'Image generated! Use Download in the context menu to save it.')
        else:
            xbmcgui.Dialog().ok('Error', result.get('error', 'Generation failed.'))
    except Exception as e:
        progress.close()
        xbmcgui.Dialog().ok('Error', str(e))


def generate_video():
    if not API_KEY:
        xbmcgui.Dialog().ok('Setup required', 'Open Settings and enter your API key from percfectai.com')
        ADDON.openSettings()
        return

    prompt = prompt_text('Describe the video you want to create')
    if not prompt:
        return

    progress = xbmcgui.DialogProgress()
    progress.create('PercfectStudios', 'Generating video… this can take 1–2 minutes')
    try:
        result = api('POST', '/api/generate/video', {'prompt': prompt})
        progress.close()
        url = result.get('url')
        if url:
            li = xbmcgui.ListItem(prompt)
            li.setInfo('video', {'title': prompt, 'plot': prompt})
            xbmc.Player().play(url, li)
        else:
            xbmcgui.Dialog().ok('Error', result.get('error', 'Generation failed.'))
    except Exception as e:
        progress.close()
        xbmcgui.Dialog().ok('Error', str(e))


def show_gallery():
    if not API_KEY:
        xbmcgui.Dialog().ok('Setup required', 'Enter your API key in Settings first.')
        return
    try:
        items = api('GET', '/api/gallery')
        for item in items:
            label = item.get('prompt', 'Untitled')[:80]
            li = xbmcgui.ListItem(label)
            li.setArt({'thumb': item['output_url']})
            li.setInfo('video', {'title': label, 'plot': item.get('prompt', '')})
            xbmcplugin.addDirectoryItem(ADDON_HANDLE, item['output_url'], li, isFolder=False)
        xbmcplugin.endOfDirectory(ADDON_HANDLE)
    except Exception as e:
        xbmcgui.Dialog().ok('Error', str(e))


action = ARGS.get('action', [''])[0]

if   not action:              main_menu()
elif action == 'generate_image': generate_image()
elif action == 'generate_video': generate_video()
elif action == 'gallery':        show_gallery()
elif action == 'settings':       ADDON.openSettings()
