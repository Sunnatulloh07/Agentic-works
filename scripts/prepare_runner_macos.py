"""Prepare an unsigned macOS user LaunchAgent bundle, never install/start it.

Run on the target Mac after Node >=22.4 and Python >=3.10 are provisioned.
No token, package installation, launchctl invocation or signing occurs here.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
from urllib.parse import urlsplit


def prepare(output, folders, server, node, python, project_root=None):
    root=Path(project_root or Path(__file__).resolve().parents[1])
    target=Path(output).expanduser().absolute()
    if target.exists():raise ValueError('Use a new output directory; existing bundle is never overwritten')
    parts=urlsplit(server)
    if parts.scheme!='wss' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Explicit credential-free WSS server required')
    paths=[]
    for folder in folders:
        p=Path(folder).expanduser().resolve(strict=True)
        if not p.is_dir() or p==Path(p.anchor):raise ValueError('Dedicated allowed folder required')
        paths.append(str(p))
    if not 1<=len(paths)<=32:raise ValueError('One to 32 approved folders required')
    runtimes=[]
    for executable in (node,python):
        p=Path(executable).expanduser()
        if not p.is_absolute() or not p.is_file() or not os.access(p,os.X_OK):raise ValueError('Absolute existing executable required')
        runtimes.append(str(p.resolve()))
    target.mkdir(parents=True,mode=0o700)
    target=target.resolve()
    app=target/'app';app.mkdir(mode=0o700)
    state=target/'state';state.mkdir(mode=0o700)
    cfg=target/'config';cfg.mkdir(mode=0o700)
    for name in ('runner.js','portable_fs.py'):
        shutil.copyfile(root/'apps'/'runner'/name,app/name);(app/name).chmod(0o600)
    (cfg/'allow.json').write_text(json.dumps({'folders':paths,'deny_always':['secret']},ensure_ascii=False,indent=2)+'\n')
    (cfg/'allow.json').chmod(0o600)
    env={'RUNNER_SERVER':server,'RUNNER_ALLOW':str(cfg/'allow.json'),'RUNNER_JOURNAL':str(state/'journal'),
         'RUNNER_STOP_FILE':str(state/'STOP'),'RUNNER_TOKEN_FILE':str(cfg/'device.token'),'RUNNER_PYTHON':runtimes[1]}
    plist={'Label':'uz.agentplatform.runner','ProgramArguments':[runtimes[0],str(app/'runner.js')],
           'WorkingDirectory':str(state),'EnvironmentVariables':env,'RunAtLoad':True,
           'KeepAlive':{'Crashed':True},'ThrottleInterval':30,'ProcessType':'Background',
           'StandardOutPath':str(state/'stdout.log'),'StandardErrorPath':str(state/'stderr.log')}
    destination=target/'uz.agentplatform.runner.plist'
    destination.write_bytes(plistlib.dumps(plist));destination.chmod(0o600)
    (target/'README-UZ.md').write_text('''# macOS runner development paketi

Bu unsigned user LaunchAgent source paketi. Signed .pkg/.dmg installer, tray UI,
notarization va Mac qurilmasidagi real acceptance bajarilgan deb hisoblanmaydi.

1. Owner device enrollment orqali token oladi va config/device.token fayliga
   faqat o‘z foydalanuvchisi o‘qiy oladigan 0600 mode bilan saqlaydi. Tokenni
   plist, terminal argumentlari, Git yoki logga joylashtirmang.
2. Node 22.4+ va Python 3.10+ yo‘llarini, WSS endpoint va allow.json’ni tekshiring.
3. Tayyor plist’ni ~/Library/LaunchAgents/ ichiga joylang; keyin macOS launchctl
   bootstrap gui/UID PLIST yo‘li orqali ishga tushiring. Generator buni bajarmaydi.
4. state/STOP fayli yangi lokal ijrolarni to‘xtatadi. Davom ettirish uchun faylni
   olib tashlash bilan birga user service’ni qayta ishga tushirish kerak.
5. Token revoke/expiry’da runner to‘xtaydi. Token faylini xavfsiz almashtirib,
   service’ni qayta ishga tushiring. Boshqa disconnectlarda qayta ulanish bor.

Ayni paytda faqat fs.list va fs.read_text. Ekran, printer, Office, shell yoki app
execution bu paketda yoqilmagan. macOS F_GETPATH yo‘li hali haqiqiy Mac’da
sinovdan o‘tkazilmagan. macOS TCC/Sandbox huquqlari avtomatik berilmaydi.
''',encoding='utf-8')
    return {'output':str(target),'plist':str(destination),'token_embedded':False,'installed':False,'signed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--folder',action='append',required=True)
    parser.add_argument('--server',required=True);parser.add_argument('--node',required=True);parser.add_argument('--python',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.output,args.folder,args.server,args.node,args.python),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
