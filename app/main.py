# -*- coding: utf-8 -*-
import re
import requests
from flask import Flask, Response, redirect, request
from requests.exceptions import (
    ChunkedEncodingError,
    ContentDecodingError, ConnectionError, StreamConsumedError)
from requests.utils import (
    stream_decode_response_unicode, iter_slices, CaseInsensitiveDict)
from urllib3.exceptions import (
    DecodeError, ReadTimeoutError, ProtocolError)
import os

# config
# 分支文件使用jsDelivr镜像的开关，0为关闭，默认关闭
jsdelivr = 0
size_limit = 1024 * 1024 * 1024 * 1  # 允许的文件大小，默认999GB，相当于无限制了 https://github.com/hunshcn/gh-proxy/issues/8

private_token = os.getenv('GH_TOKEN')
"""
  先生效白名单再匹配黑名单，pass_list匹配到的会直接302到jsdelivr而忽略设置
  生效顺序 白->黑->pass，可以前往https://github.com/hunshcn/gh-proxy/issues/41 查看示例
  每个规则一行，可以封禁某个用户的所有仓库，也可以封禁某个用户的特定仓库，下方用黑名单示例，白名单同理
  user1 # 封禁user1的所有仓库
  user1/repo1 # 封禁user1的repo1
  */repo1 # 封禁所有叫做repo1的仓库
"""
white_list = '''
'''
black_list = '''
'''
pass_list = '''
'''
html_str = """
<!DOCTYPE html>
<html lang="zh-Hans">
<style>
    html, body {
        width: 100%;
        margin: 0;
    }

    html {
        height: 100%;
    }

    body {
        min-height: 100%;
        padding: 20px;
        box-sizing: border-box;
    }

    p {
        word-break: break-all;
    }

    @media (max-width: 500px) {
        h1 {
            margin-top: 80px;
        }
    }

    .flex {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }

    .block {
        display: block;
        position: relative;
    }

    .url {
        font-size: 18px;
        padding: 10px 10px 10px 5px;
        position: relative;
        width: 300px;
        border: none;
        border-bottom: 1px solid #bfbfbf;
    }

    input:focus {
        outline: none;
    }

    .bar {
        content: '';
        height: 2px;
        width: 100%;
        bottom: 0;
        position: absolute;
        background: #00bfb3;
        transition: 0.2s ease transform;
        -moz-transition: 0.2s ease transform;
        -webkit-transition: 0.2s ease transform;
        transform: scaleX(0);
    }

    .url:focus ~ .bar {
        transform: scaleX(1);
    }

    .btn {
        line-height: 38px;
        background-color: #00bfb3;
        color: #fff;
        white-space: nowrap;
        text-align: center;
        font-size: 14px;
        border: none;
        border-radius: 2px;
        cursor: pointer;
        padding: 5px;
        width: 160px;
        margin: 30px 0;
    }

    .tips, .example {
        color: #7b7b7b;
        position: relative;
        align-self: flex-start;
        margin-left: 7.5em;
    }

    .tips > p:first-child::before {
        position: absolute;
        left: -3em;
        content: 'PS：';
        color: #7b7b7b
    }

    .example > p:first-child::before {
        position: absolute;
        left: -7.5em;
        content: '合法输入示例：';
        color: #7b7b7b
    }
</style>
<head>
    <meta charset="utf-8">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <meta name="viewport" content="width=device-width,initial-scale=1.0">
    <script>
        function toSubmit(e) {
            e.preventDefault()
            window.open(location.href.substr(0, location.href.lastIndexOf('/') + 1) + document.getElementsByName('q')[0].value);
            return false
        }
    </script>
    <title>GitHub 文件加速</title>
</head>
<body class="flex">
<h1 style="margin-bottom: 50px">GitHub 文件加速</h1>
<form action="./" method="get" style="padding-bottom: 40px" target="_blank" class="flex" onsubmit="toSubmit(event)">
    <label class="block" style="width: fit-content">
                <input class="block url" name="q" type="text" placeholder="键入Github文件链接"
                               pattern="^((https|http):\/\/)?(github\.com\/.+?\/.+?\/(?:releases|archive|blob|raw|suites)|((?:raw|gist)\.(?:githubusercontent|github)\.com))\/.+$" required>
        <div class="bar"></div>
    </label>
    <input class="block btn" type="submit" value="下载">
</form>
<p style="position: sticky;top: calc(100% - 2.5em);">My Blob: <a style="color: #3294ea"
                                                                                         href="https://www.dtl-mirros.top/">follow</a> 项目基于开源于GitHub: <a style="color: #3294ea"
                                                                                         href="https://github.com/hunshcn/gh-proxy">hunshcn/gh-proxy</a>
</p>
</body>
</html>

"""
HOST = '127.0.0.1'  # 监听地址，建议监听本地然后由web服务器反代
PORT = 9002  # 监听端口
ASSET_URL = 'https://hunshcn.github.io/gh-proxy'  # 主页
#proxies = {
#        'http': 'socks5h://127.0.0.1:20171',  # 本地的代理转发
#        'https': 'socks5h://127.0.0.1:20171'
#        }

white_list = [tuple([x.replace(' ', '') for x in i.split('/')]) for i in white_list.split('\n') if i]
black_list = [tuple([x.replace(' ', '') for x in i.split('/')]) for i in black_list.split('\n') if i]
pass_list = [tuple([x.replace(' ', '') for x in i.split('/')]) for i in pass_list.split('\n') if i]
app = Flask(__name__)
CHUNK_SIZE = 1024 * 10
# index_html = requests.get(ASSET_URL, timeout=10).text
icon_r = requests.get(ASSET_URL + '/favicon.ico', timeout=10).content
exp1 = re.compile(r'^(?:https?://)?github\.com/(?P<author>.+?)/(?P<repo>.+?)/(?:releases|archive)/.*$')
exp2 = re.compile(r'^(?:https?://)?github\.com/(?P<author>.+?)/(?P<repo>.+?)/(?:blob|raw)/.*$')
exp3 = re.compile(r'^(?:https?://)?github\.com/(?P<author>.+?)/(?P<repo>.+?)/(?:info|git-).*$')
exp4 = re.compile(r'^(?:https?://)?raw\.(?:githubusercontent|github)\.com/(?P<author>.+?)/(?P<repo>.+?)/.+?/.+$')
exp5 = re.compile(r'^(?:https?://)?gist\.(?:githubusercontent|github)\.com/(?P<author>.+?)/.+?/.+$')

requests.sessions.default_headers = lambda: CaseInsensitiveDict()


@app.route('/')
def index():
    if 'q' in request.args:
        return redirect('/' + request.args.get('q'))
    return Response(html_str)


@app.route('/favicon.ico')
def icon():
    return Response(icon_r, content_type='image/vnd.microsoft.icon')


def iter_content(self, chunk_size=1, decode_unicode=False):
    """rewrite requests function, set decode_content with False"""

    def generate():
        # Special case for urllib3.
        if hasattr(self.raw, 'stream'):
            try:
                for chunk in self.raw.stream(chunk_size, decode_content=False):
                    yield chunk
            except ProtocolError as e:
                raise ChunkedEncodingError(e)
            except DecodeError as e:
                raise ContentDecodingError(e)
            except ReadTimeoutError as e:
                raise ConnectionError(e)
        else:
            # Standard file-like object.
            while True:
                chunk = self.raw.read(chunk_size)
                if not chunk:
                    break
                yield chunk

        self._content_consumed = True

    if self._content_consumed and isinstance(self._content, bool):
        raise StreamConsumedError()
    elif chunk_size is not None and not isinstance(chunk_size, int):
        raise TypeError("chunk_size must be an int, it is instead a %s." % type(chunk_size))
    # simulate reading small chunks of the content
    reused_chunks = iter_slices(self._content, chunk_size)

    stream_chunks = generate()

    chunks = reused_chunks if self._content_consumed else stream_chunks

    if decode_unicode:
        chunks = stream_decode_response_unicode(chunks, self)

    return chunks


def check_url(u):
    for exp in (exp1, exp2, exp3, exp4, exp5):
        m = exp.match(u)
        if m:
            return m
    return False


@app.route(f'/{private_token}/<path:u>', methods=['GET', 'POST'])
@app.route('/<path:u>', methods=['GET'])
def handler(u):
    u = u if u.startswith('http') else 'https://' + u
    if u.rfind('://', 3, 9) == -1:
        u = u.replace('s:/', 's://', 1)  # uwsgi会将//传递为/
    pass_by = False
    m = check_url(u)
    if m:
        m = tuple(m.groups())
        if white_list:
            for i in white_list:
                if m[:len(i)] == i or i[0] == '*' and len(m) == 2 and m[1] == i[1]:
                    break
            else:
                return Response('Forbidden by white list.', status=403)
        for i in black_list:
            if m[:len(i)] == i or i[0] == '*' and len(m) == 2 and m[1] == i[1]:
                return Response('Forbidden by black list.', status=403)
        for i in pass_list:
            if m[:len(i)] == i or i[0] == '*' and len(m) == 2 and m[1] == i[1]:
                pass_by = True
                break
    else:
        return Response('Invalid input.', status=403)

    if (jsdelivr or pass_by) and exp2.match(u):
        u = u.replace('/blob/', '@', 1).replace('github.com', 'cdn.jsdelivr.net/gh', 1)
        return redirect(u)
    elif (jsdelivr or pass_by) and exp4.match(u):
        u = re.sub(r'(\.com/.*?/.+?)/(.+?/)', r'\1@\2', u, 1)
        _u = u.replace('raw.githubusercontent.com', 'cdn.jsdelivr.net/gh', 1)
        u = u.replace('raw.github.com', 'cdn.jsdelivr.net/gh', 1) if _u == u else _u
        return redirect(u)
    else:
        if exp2.match(u):
            u = u.replace('/blob/', '/raw/', 1)
        if pass_by:
            url = u + request.url.replace(request.base_url, '', 1)
            if url.startswith('https:/') and not url.startswith('https://'):
                url = 'https://' + url[7:]
            return redirect(url)
        return proxy(u)


def proxy(u, allow_redirects=False):
    headers = {}
    r_headers = dict(request.headers)
    if 'Host' in r_headers:
        r_headers.pop('Host')
    try:
        url = u + request.url.replace(request.base_url, '', 1)
        if url.startswith('https:/') and not url.startswith('https://'):
            url = 'https://' + url[7:]
        print(url)
        r = requests.request(method=request.method, url=url, data=request.data, headers=r_headers, stream=True, allow_redirects=allow_redirects)
        headers = dict(r.headers)

        if 'Content-length' in r.headers and int(r.headers['Content-length']) > size_limit:
            return redirect(u + request.url.replace(request.base_url, '', 1))

        def generate():
            for chunk in iter_content(r, chunk_size=CHUNK_SIZE):
                yield chunk

        if 'Location' in r.headers:
            _location = r.headers.get('Location')
            if check_url(_location):
                headers['Location'] = '/' + _location
            else:
                return proxy(_location, True)

        return Response(generate(), headers=headers, status=r.status_code)
    except Exception as e:
        import traceback
        traceback.print_exc()
        headers['content-type'] = 'text/html; charset=UTF-8'
        return Response('server error ' + str(e), status=500, headers=headers)

app.debug = True
if __name__ == '__main__':
    app.run(host=HOST, port=PORT)
