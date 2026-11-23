"""Repeatable Windows onedir + Velopack artifacts. Never publishes or overwrites a release."""
import argparse
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def defaults(directory,release):
    from services.settings import default_settings
    from translator.dictionary import DEFAULT_DICTIONARY
    settings=replace(default_settings(),deepseek_api_key='',web_search_api_key='',deepseek_base_url='https://api.deepseek.com',
        deepseek_model='',model_candidates=[],sqlite_path='',output_dir='',start_date='',end_date='',launch_start_date='',launch_end_date='',release_mode=True)
    value=asdict(settings);value['models']=[];value['model']='';write(directory/'config.json',value)
    write(directory/'release.json',release);write(directory/'translator/dictionary.json',DEFAULT_DICTIONARY)
    for name in ('pending_terms.json','translation_cache.json'):write(directory/'translator'/name,{})
    # Explicit application resources; user-created presets/assets and private workspace files are excluded.
    for folder in ('assets','prompts'):
        names=subprocess.check_output(['git','ls-files',folder],cwd=ROOT,text=True).splitlines()
        for name in names:
            source=ROOT/name;target=directory/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)


def audit(directory):
    forbidden={'cache','output','news-images','video-downloads','models','environments','sspo-animation'}
    paths=[p for p in directory.rglob('*') if p.is_file()]
    for p in paths:
        if forbidden & set(p.relative_to(directory).parts) or p.suffix.lower() in ('.safetensors','.whl','.db','.bin'):
            raise ValueError('Unexpected private/runtime payload: '+str(p.relative_to(directory)))
    data=json.loads((directory/'_internal/config.json').read_text(encoding='utf-8'))
    if data['deepseek_api_key'] or data['web_search_api_key'] or data['output_dir'] or data['sqlite_path']:raise ValueError('Build configuration was not sanitized')
    return paths


def installer_splash(path):
    from PIL import Image, ImageDraw, ImageFont
    picture=Image.new('RGB',(640,280),'#f5f6f9');draw=ImageDraw.Draw(picture)
    font=ROOT/'assets/fonts/NotoSansCJKsc-Bold.otf'
    draw.rounded_rectangle((12,12,628,268),radius=12,fill='white')
    for position,text,size,color in (
        ((40,32),'SpaceWeekly',30,'#4468e8'),
        ((40,94),'正在安装 SpaceWeekly',26,'#222b3d'),
        ((40,150),'安装完成后将自动打开软件。',18,'#65738b'),
        ((40,184),'工作区、语音环境与模型独立保存。',18,'#65738b')):
        draw.text(position,text,font=ImageFont.truetype(str(font),size),fill=color)
    picture.save(path)


def msi_backgrounds(directory):
    from PIL import Image
    for name,size in (('msi-banner.bmp',(493,58)),('msi-background.bmp',(493,312))):
        Image.new('RGB',size,'white').save(directory/name)


def compile_uninstaller(stage,work,release):
    compiler=Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():raise ValueError('Build requires Windows .NET Framework C# compiler')
    identity=work/'Uninstall.Identity.cs'
    identity.write_text('using System.Reflection;\n[assembly:AssemblyTitle("SpaceWeekly Uninstall")]\n'
        '[assembly:AssemblyFileVersion("'+release['version'].split('-')[0]+'")]\n'
        'internal static partial class Uninstall { internal const string PackageId="'+release['package_id']+'"; }',encoding='utf-8')
    command=[str(compiler),'/nologo','/codepage:65001','/platform:x64','/target:winexe','/optimize+',
        '/r:System.Windows.Forms.dll','/r:System.Drawing.dll','/win32icon:'+str(ROOT/'assets/sspo.ico'),
        '/win32manifest:'+str(ROOT/'tools/windows/uninstall.manifest'),'/out:'+str(stage/'Uninstall.exe'),
        str(ROOT/'tools/windows/Uninstall.cs'),str(identity)]
    subprocess.run(command,check=True,cwd=ROOT)
    assert (stage/'Uninstall.exe').read_bytes()[:2]==b'MZ'


def main():
    from config import app_version
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--vpk',type=Path,default=ROOT/'build/release-tools/vpk.exe')
    p.add_argument('--qa-version');p.add_argument('--reuse-onedir',type=Path);p.add_argument('--previous-feed',type=Path);p.add_argument('--no-msi',action='store_true');args=p.parse_args()
    version=args.qa_version or app_version();qa=bool(args.qa_version)
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-qa\.\d+)?',version) or qa and not version.startswith(app_version()+'-qa.'):raise ValueError('QA versions must derive from config.app_version(); stable version cannot be overridden')
    output=args.output.resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('Output is not empty; choose a new directory to preserve prior artifacts')
    output.mkdir(parents=True,exist_ok=True)
    release=dict(version=version,package_id='Starship30.SpaceWeekly'+('.QA' if qa else ''),channel='qa-win-x64' if qa else 'win-x64',
        runtime='win-x64',qa=qa,repository='https://github.com/Starship30/SpaceWeekly',sdk='1.2.161',signed=False)
    work=ROOT/'build'/('installer-'+uuid4().hex[:12]);work.mkdir(parents=True)
    packages=work/'packages';packages.mkdir()
    (work/'tmp').mkdir()
    resources=work/'resources';defaults(resources,release)
    if args.reuse_onedir:
        stage=work/'SpaceWeekly';shutil.copytree(args.reuse_onedir.resolve(),stage)
        write(stage/'_internal/release.json',release)
    else:
        env=dict(os.environ,SPACEWEEKLY_RELEASE_RESOURCES=str(resources),PYINSTALLER_CONFIG_DIR=str(work/'pyi-cache'),PYTHONNOUSERSITE='1',TEMP=str(work/'tmp'),TMP=str(work/'tmp'))
        with (work/'pyinstaller.log').open('wb') as log:
            subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--workpath',str(work/'pyi'),'--distpath',str(work/'dist'),str(ROOT/'SpaceWeekly.spec')],cwd=ROOT,env=env,stdout=log,stderr=log,check=True)
        stage=work/'dist/SpaceWeekly'
    compile_uninstaller(stage,work,release)
    for name in ('USER_AGREEMENT.zh-CN.md','THIRD_PARTY_NOTICES.md','WIX-LICENSE.txt'):
        shutil.copy2(ROOT/'tools/windows'/name,stage/name)
    paths=audit(stage)
    if args.previous_feed:
        for old in args.previous_feed.glob('*'):
            if old.suffix=='.nupkg' or old.name.startswith('releases.'):shutil.copy2(old,packages/old.name)
    notes=ROOT/'tools'/('WINDOWS_QA_NOTES.md' if qa else 'RELEASE_NOTES_'+app_version()+'.md')
    splash=work/'installer-splash.png';installer_splash(splash)
    msi_backgrounds(work)
    command=[str(args.vpk.resolve()),'pack','--packId',release['package_id'],'--packVersion',version,'--packDir',str(stage),'--mainExe','SpaceWeekly.exe',
        '--runtime','win-x64','--channel',release['channel'],'--packTitle','SpaceWeekly QA' if qa else 'SpaceWeekly','--icon',str(ROOT/'assets/sspo.ico'),
        '--releaseNotes',str(notes),'--shortcuts','Desktop,StartMenuRoot','--splashImage',str(splash),
        '--splashProgressColor','#4468e8','--outputDir',str(packages)]
    msi_version=app_version()
    if qa:
        parts=msi_version.split('.');parts[-1]=str(int(parts[-1])+int(version.rsplit('.',1)[-1]));msi_version='.'.join(parts)
    if not args.no_msi:
        from tools.msi_wizard import rtf
        agreement=work/'agreement.rtf'
        agreement.write_text(rtf((ROOT/'tools/windows/USER_AGREEMENT.zh-CN.md').read_text(encoding='utf-8')),encoding='ascii')
        command+=['--msi','--instLocation','PerUser','--msiVersion',msi_version,
            '--instLicense',str(agreement),
            '--msiTopBanner',str(work/'msi-banner.bmp'),'--msiDialogBackground',str(work/'msi-background.bmp')]
    # Signing secrets are passed only by vpk's documented environment options; never bundled.
    package_env=dict(os.environ,TEMP=str(work/'tmp'),TMP=str(work/'tmp'))
    subprocess.run(command,cwd=ROOT,env=package_env,check=True)
    if not args.no_msi:
        from tools.msi_wizard import customize,bootstrap
        msi=next(packages.glob('*.msi'));exe=next(packages.glob('*Setup.exe'))
        customize(msi,work,release)
        bootstrap(msi,exe,work,release)
    for artifact in packages.iterdir():
        if artifact.is_file():shutil.copy2(artifact,output/artifact.name)
    write(output/'build-info.json',dict(release=release,onedir=str(stage),work=str(work),files=len(paths),bytes=sum(p.stat().st_size for p in paths),
        signing='Not configured; SHA256 is integrity, not publisher identity',python=sys.version,
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        installer_sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools/windows').iterdir() if p.is_file()}))
    checks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file() and p.name!='SHA256SUMS.txt'}
    (output/'SHA256SUMS.txt').write_text(''.join(digest+'  '+name+'\n' for name,digest in checks.items()),encoding='utf-8')
    print('Build artifacts: '+str(output));print('Onedir: '+str(stage))


if __name__=='__main__':main()
