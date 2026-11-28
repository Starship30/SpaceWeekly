"""Customize the framework-generated MSI, keeping its installation and update engine."""
import hashlib
from pathlib import Path
import re
import subprocess
import os


def rtf(text):
    result=[]
    for char in text:
        code=ord(char)
        if char=='\n':result.append(r'\par ')
        elif char in '\\{}':result.append('\\'+char)
        elif code>127:result.append('\\u'+str(code if code<32768 else code-65536)+'?')
        else:result.append(char)
    return r'{\rtf1\ansi\ansicpg936\deff0{\fonttbl{\f0 Microsoft YaHei;}}\f0\fs20 '+''.join(result)+'}'


class Database:
    def __init__(self,path):
        import _msi
        self.api=_msi;self.db=_msi.OpenDatabase(str(path),_msi.MSIDBOPEN_TRANSACT)

    def execute(self,sql,values=()):
        view=self.db.OpenView(sql)
        record=self.api.CreateRecord(len(values))
        for n,value in enumerate(values,1):
            if isinstance(value,Path):record.SetStream(n,str(value))
            elif isinstance(value,int):record.SetInteger(n,value)
            elif value is not None:record.SetString(n,str(value))
        view.Execute(record if values else None)
        return view

    def rows(self,sql):
        view=self.execute(sql);rows=[]
        while record:=view.Fetch():
            rows.append([record.GetString(n) for n in range(1,record.GetFieldCount()+1)])
        view.Close();return rows

    def insert(self,table,columns,values):
        self.execute('INSERT INTO `'+table+'` ('+','.join('`'+c+'`' for c in columns)+') VALUES ('+','.join('?' for _ in values)+')',values).Close()

    def property(self,key,value):
        self.execute('DELETE FROM `Property` WHERE `Property`=?',(key,)).Close()
        if value is not None:self.insert('Property',('Property','Value'),(key,value))


def customize(msi,work,release):
    root=Path(__file__).resolve().parents[1]
    codepage=work/'_ForceCodepage.idt';codepage.write_bytes(b'\r\n\r\n936\t_ForceCodepage\r\n')
    import ctypes
    from ctypes import wintypes
    api=ctypes.WinDLL('msi');handle=wintypes.UINT()
    api.MsiOpenDatabaseW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.POINTER(wintypes.UINT)]
    api.MsiDatabaseImportW.argtypes=[wintypes.UINT,wintypes.LPCWSTR,wintypes.LPCWSTR]
    code=api.MsiOpenDatabaseW(str(msi),ctypes.cast(ctypes.c_void_p(1),wintypes.LPCWSTR),ctypes.byref(handle))
    if code:raise OSError(code,'无法打开 MSI')
    try:
        code=api.MsiDatabaseImportW(handle,str(work),'_ForceCodepage.idt')
        if code:raise OSError(code,'无法设置中文代码页')
        if api.MsiDatabaseCommit(handle):raise ValueError('无法保存中文代码页')
    finally:api.MsiCloseHandle(handle)
    db=Database(msi)
    db.property('ProductLanguage','2052')
    db.property('LicenseAccepted',None)
    db.property('SW_DESKTOP','1')
    db.property('WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT','安装完成后启动 SpaceWeekly')
    db.execute("UPDATE `TextStyle` SET `FaceName`='Microsoft YaHei UI'").Close()
    # Native locale initialization would overwrite the explicitly reviewed Chinese strings.
    db.execute("DELETE FROM `InstallUISequence` WHERE `Action`='RustSetLocaleStrings'").Close()
    strings={
        'DlgTitle':'SpaceWeekly 安装向导','WelcomeTitle':'欢迎安装 [ProductName]',
        'WelcomeDescription':'本向导将安装 [ProductName] [ProductVersion]。点击下一步继续。',
        'WelcomeUpdateDescription':'本向导将更新现有 SpaceWeekly，沿用已有安装位置。',
        'LicenseTitle':'用户协议（测试草案）','LicenseDescription':'请阅读协议；同意后才能继续安装。',
        'LicenseCheckbox':'我已阅读并同意本测试草案',
        'BtnBack':'上一步','BtnNext':'下一步','BtnCancel':'取消','BtnFinish':'完成',
        'BtnOk':'确定','BtnYes':'是','BtnNo':'否','BtnIgnore':'忽略','BtnRetry':'重试',
        'CancelText':'确定取消本次安装吗？','ExitTitle':'安装已完成','ExitDescription':'SpaceWeekly 已安装。点击完成关闭向导。',
        'ReadyInstallTitle':'确认安装','ReadyInstallText':'安装目录：[INSTALLFOLDER]。点击安装开始。工作区和模型分别保存。',
        'BrowseTitle':'选择安装文件夹','BrowseDescription':'请选择程序安装位置。','BrowsePathLabel':'文件夹路径',
        'BrowseComboLabel':'位置','BrowseUpTooltip':'上一级','BrowseNewFolderTooltip':'新建文件夹',
        'DiskCostTitle':'磁盘空间','DiskCostDescription':'查看各磁盘所需和可用空间。','DiskCostText':'安装前请确认有足够的剩余空间。',
        'InvalidDirText':'安装路径无效或无法写入，请选择其他本地文件夹。',
        'FilesInUseTitle':'文件正在使用','FilesInUseDescription':'请先关闭正在使用安装文件的程序。',
        'FilesInUseText':'以下程序正在运行：','FilesInUseExit':'关闭相关程序后重试。',
        'RmFilesInUseTitle':'程序正在运行','RmFilesInUseDescription':'请先保存工作并关闭 SpaceWeekly。',
        'RmFilesInUseText':'继续前请安全结束后台任务。',
        'PrepareTitle':'准备安装','PrepareDescription':'正在检查安装所需条件。','ProgressStatus':'当前状态：',
        'FatalTitle':'安装未完成','FatalDescription1':'安装发生错误。请查看安装日志并重试。','FatalDescription2':'点击完成退出。',
        'UserExitTitle':'安装已取消','UserExitDescription1':'用户取消了安装。','UserExitDescription2':'点击完成退出。',
        'MaintWelcomeTitle':'维护 SpaceWeekly','MaintWelcomeDescription':'可修复或卸载现有程序。',
        'MaintTypeTitle':'选择维护操作','MaintTypeDescription':'程序数据和模型独立于安装目录。',
        'MaintChangeButton':'更改','MaintChangeText':'更改已安装的程序功能。','MaintChangeDisabled':'本产品无需更改功能。',
        'MaintRepairButton':'修复','MaintRepairText':'修复已有安装文件。','MaintRepairDisabled':'当前无法修复。',
        'MaintRemoveButton':'卸载','MaintRemoveText':'移除程序，保留工作区与独立模型。','MaintRemoveDisabled':'当前无法卸载。',
        'ResumeTitle':'继续安装','ResumeDescription':'继续上次未完成的安装。','ResumeBtnInstall':'继续'
    }
    for action,label in (('Install','安装'),('Change','更改'),('Remove','卸载'),('Repair','修复'),('Update','更新')):
        strings['ReadyBtn'+action]=label
        strings.setdefault('Ready'+action+'Title','确认'+label)
        strings.setdefault('Ready'+action+'Text','点击'+label+'继续。')
        present={'Install':'Installing','Change':'Changing','Remove':'Removing','Repair':'Repairing','Update':'Updating'}[action]
        strings['Progress'+present+'Title']='正在'+label+' SpaceWeekly'
        strings['Progress'+present+'Text']='请等待操作完成，可以取消。'
    for key,value in strings.items():
        value=value.replace('[ProductName]','SpaceWeekly').replace('[ProductVersion]',release['version'])
        db.property('Msi'+key,value)
    # MSI expands a formatted control once, not recursively through another property.
    db.execute("UPDATE `Control` SET `Text`=? WHERE `Text`='[MsiReadyInstallText]'",(strings['ReadyInstallText'],)).Close()
    keys=set(re.findall(r'\[(Msi\w+)\]',' '.join(r[0] for r in db.rows("SELECT `Text` FROM `Control` WHERE `Type` <> 'ScrollableText'"))))
    missing=keys-{'Msi'+k for k in strings}
    if missing:raise ValueError('Missing installer translations: '+str(missing))
    cols=('Dialog','HCentering','VCentering','Width','Height','Attributes','Title','Control_First','Control_Default','Control_Cancel')
    db.insert('Dialog',cols,('SpaceWeeklyDirDlg',50,50,370,270,3,'[MsiDlgTitle]','Path','Next','Cancel'))
    controls=('Dialog_','Control','Type','X','Y','Width','Height','Attributes','Property','Text','Control_Next','Help')
    def control(name,kind,x,y,width,height,text,prop=None,next_control=None,attrs=3):
        db.insert('Control',controls,('SpaceWeeklyDirDlg',name,kind,x,y,width,height,attrs,prop,text,next_control,None))
    control('Title','Text',15,10,340,22,'选择程序安装位置')
    control('Hint','Text',20,43,330,37,'工作区、词典、音色和模型独立保存。升级沿用原目录；迁移请另行备份后操作。')
    control('Path','PathEdit',20,88,270,20,None,'INSTALLFOLDER','Browse')
    control('Browse','PushButton',297,88,53,20,'浏览',next_control='Space')
    control('Space','PushButton',20,124,100,20,'查看所需及可用空间',next_control='Desktop')
    control('Desktop','CheckBox',20,165,300,20,'创建桌面快捷方式','SW_DESKTOP','Back')
    db.insert('CheckBox',('Property','Value'),('SW_DESKTOP','1'))
    control('Back','PushButton',170,243,56,17,'上一步',next_control='Next')
    control('Next','PushButton',236,243,56,17,'下一步',next_control='Cancel')
    control('Cancel','PushButton',304,243,56,17,'取消',next_control='Path')
    eventcols=('Dialog_','Control_','Event','Argument','Condition','Ordering')
    def event(dialog,button,event,value,condition='1',order=1):
        db.insert('ControlEvent',eventcols,(dialog,button,event,value,condition,order))
    def reroute(where,target):
        rows=db.rows('SELECT * FROM `ControlEvent` WHERE '+where)
        if not rows:raise ValueError('MSI 向导路径与预期不符')
        db.execute('DELETE FROM `ControlEvent` WHERE '+where).Close()
        for row in rows:
            row[3]=target;row[-1]=int(row[-1]);db.insert('ControlEvent',eventcols,row)
    reroute("`Dialog_`='WelcomeDlg' AND `Control_`='Next' AND `Event`='NewDialog' AND `Condition`='NOT Installed'",'LicenseAgreementDlg')
    reroute("`Dialog_`='LicenseAgreementDlg' AND `Control_`='Next' AND `Event`='NewDialog'",'SpaceWeeklyDirDlg')
    reroute("`Dialog_`='VerifyReadyDlg' AND `Control_`='Back' AND `Condition`='NOT Installed'",'SpaceWeeklyDirDlg')
    event('SpaceWeeklyDirDlg','Back','NewDialog','LicenseAgreementDlg')
    event('SpaceWeeklyDirDlg','Cancel','SpawnDialog','CancelDlg')
    event('SpaceWeeklyDirDlg','Browse','[_BrowseProperty]','INSTALLFOLDER',order=1)
    event('SpaceWeeklyDirDlg','Browse','SpawnDialog','BrowseDlg',order=2)
    event('SpaceWeeklyDirDlg','Space','SetTargetPath','INSTALLFOLDER',order=1)
    event('SpaceWeeklyDirDlg','Space','SpawnDialog','DiskCostDlg',order=2)
    event('SpaceWeeklyDirDlg','Next','SetTargetPath','INSTALLFOLDER',order=1)
    event('SpaceWeeklyDirDlg','Next','DoAction','RustValidatePath',order=2)
    event('SpaceWeeklyDirDlg','Next','[VELOPACK_INSTALLDIR]','[INSTALLFOLDER]','WIXUI_INSTALLDIR_VALID <> "0"',3)
    event('SpaceWeeklyDirDlg','Next','NewDialog','VerifyReadyDlg','WIXUI_INSTALLDIR_VALID <> "0"',4)
    # Desktop preference conditions only the known framework shortcut component.
    db.execute("UPDATE `Component` SET `Condition`='SW_DESKTOP=1' WHERE `Component`='ApplicationDesktopShortcut'").Close()
    registry='Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MSI:'+release['package_id']
    tables={r[0] for r in db.rows('SELECT `Name` FROM `_Tables`')}
    if 'RegLocator' not in tables:
        db.execute('CREATE TABLE `RegLocator` (`Signature_` CHAR(72) NOT NULL, `Root` SHORT NOT NULL, `Key` CHAR(255) NOT NULL, `Name` CHAR(255), `Type` SHORT PRIMARY KEY `Signature_`)').Close()
    if 'AppSearch' not in tables:
        db.execute('CREATE TABLE `AppSearch` (`Property` CHAR(72) NOT NULL, `Signature_` CHAR(72) NOT NULL PRIMARY KEY `Property`, `Signature_`)').Close()
    if 'Signature' not in tables:
        db.execute('CREATE TABLE `Signature` (`Signature` CHAR(72) NOT NULL, `FileName` CHAR(255) NOT NULL, `MinVersion` CHAR(20), `MaxVersion` CHAR(20), `MinSize` LONG, `MaxSize` LONG, `MinDate` LONG, `MaxDate` LONG, `Languages` CHAR(255) PRIMARY KEY `Signature`)').Close()
        db.insert('Signature',('Signature','FileName'),('SW_UnusedFileSignature','SpaceWeekly.exe'))
    db.insert('RegLocator',('Signature_','Root','Key','Name','Type'),('SW_Existing',1,registry,'InstallLocation',18))
    db.insert('AppSearch',('Property','Signature_'),('SW_EXISTINGDIR','SW_Existing'))
    db.insert('RegLocator',('Signature_','Root','Key','Name','Type'),('SW_Legacy',1,registry.replace('MSI:',''),'InstallLocation',18))
    db.insert('AppSearch',('Property','Signature_'),('SW_LEGACYDIR','SW_Legacy'))
    db.insert('CustomAction',('Action','Type','Source','Target'),('SW_FindLegacy',51,'SW_EXISTINGDIR','[SW_LEGACYDIR]'))
    for table in ('InstallUISequence','InstallExecuteSequence'):
        if not db.rows('SELECT `Action` FROM `'+table+"` WHERE `Action`='AppSearch'"):
            db.insert(table,('Action','Condition','Sequence'),('AppSearch','',50))
        db.insert(table,('Action','Condition','Sequence'),('SW_FindLegacy','NOT SW_EXISTINGDIR AND SW_LEGACYDIR',52))
    db.insert('CustomAction',('Action','Type','Source','Target'),('SW_KeepDirectory',51,'VELOPACK_INSTALLDIR','[SW_EXISTINGDIR]'))
    db.insert('InstallUISequence',('Action','Condition','Sequence'),('SW_KeepDirectory','SW_EXISTINGDIR',53))
    db.insert('InstallExecuteSequence',('Action','Condition','Sequence'),('SW_KeepDirectory','SW_EXISTINGDIR',53))
    for name in ('Path','Browse'):
        db.insert('ControlCondition',('Dialog_','Control_','Action','Condition'),('SpaceWeeklyDirDlg',name,'Disable','SW_EXISTINGDIR'))
    secure=db.rows("SELECT `Value` FROM `Property` WHERE `Property`='SecureCustomProperties'")[0][0]
    db.property('SecureCustomProperties',secure+';SW_DESKTOP;SW_EXISTINGDIR;SW_LEGACYDIR')
    summary=db.db.GetSummaryInformation(3);summary.SetProperty(1,936);summary.SetProperty(7,'x64;2052');summary.Persist()
    db.db.Commit()
    del summary,db
    embed_ui(msi,work,release)


def embed_ui(msi,work,release):
    root=Path(__file__).resolve().parents[1]
    work.mkdir(parents=True,exist_ok=True)
    import urllib.request
    from zipfile import ZipFile
    directory=root/'build/release-tools/dtf-4.0.6'
    for name,expected in (
        ('customaction','c68e10871b61a3775b0da578dac030f822a62058d32152382c070bed25560786'),
        ('windowsinstaller','bf013d52f9386fab124f0caf227fd70768b3eb15f93267de43beb36b21f95af6')):
        archive=directory/(name+'.nupkg');archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.is_file():
            with urllib.request.urlopen('https://api.nuget.org/v3-flatcontainer/wixtoolset.dtf.'+name+'/4.0.6/wixtoolset.dtf.'+name+'.4.0.6.nupkg',timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('WiX DTF 构建组件校验失败：'+name)
        with ZipFile(archive) as zipped:zipped.extractall(directory/name)
    net=Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319'
    dtf=directory/'windowsinstaller/lib/net20/WixToolset.Dtf.WindowsInstaller.dll'
    managed=work/'InstallerUI.dll';native=work/'InstallerUI.Native.dll'
    subprocess.run([str(net/'csc.exe'),'/nologo','/codepage:65001','/target:library','/platform:x64','/optimize+',
        '/out:'+str(managed),'/r:'+str(dtf),'/r:System.Xaml.dll','/r:System.Windows.Forms.dll',
        *['/r:'+str(net/'WPF'/name) for name in ('WindowsBase.dll','PresentationCore.dll','PresentationFramework.dll')],
        '/resource:'+str(root/'tools/windows/Installer.xaml')+',Installer.xaml',
        '/resource:'+str(root/'assets/brand/sspo-color.png')+',BrandColor.png',
        '/resource:'+str(root/'assets/brand/sspo-white.png')+',BrandWhite.png',
        '/resource:'+str(root/'assets/sspo.ico')+',App.ico',
        '/resource:'+str(root/'tools/windows/USER_AGREEMENT.zh-CN.md')+',Agreement.md',str(root/'tools/windows/Installer.cs')],check=True)
    packager=directory/'customaction/tools'
    subprocess.run([str(packager/'WixToolset.Dtf.MakeSfxCA.exe'),str(native),str(packager/'x64/SfxCA.dll'),str(managed),
        str(dtf),str(root/'tools/windows/EmbeddedUI.config')],check=True)
    db=Database(msi)
    db.property('SW_PACKAGE_ID',release['package_id']);db.property('SW_DISPLAY_VERSION',release['version'])
    db.property('MsiLogging','voicewarmupx')
    tables={r[0] for r in db.rows('SELECT `Name` FROM `_Tables`')}
    if 'MsiEmbeddedUI' not in tables:
        db.execute('CREATE TABLE `MsiEmbeddedUI` (`MsiEmbeddedUI` CHAR(72) NOT NULL, `FileName` CHAR(255) NOT NULL, `Attributes` SHORT NOT NULL, `MessageFilter` LONG NOT NULL, `Data` OBJECT NOT NULL PRIMARY KEY `MsiEmbeddedUI`)').Close()
    else:db.execute('DELETE FROM `MsiEmbeddedUI`').Close()
    db.insert('MsiEmbeddedUI',('MsiEmbeddedUI','FileName','Attributes','MessageFilter','Data'),('SpaceWeeklyUI','InstallerUI.Native.dll',3,201359327,native))
    configure_agreement(db)
    summary=db.db.GetSummaryInformation(1);summary.SetProperty(14,500);summary.Persist();db.db.Commit()


def configure_agreement(db):
    secure=db.rows("SELECT `Value` FROM `Property` WHERE `Property`='SecureCustomProperties'")[0][0]
    approval='SW_WIZARD_APPROVED;SW_ACCEPT_AGREEMENT;SW_NATIVE_APPROVED;LicenseAccepted'
    db.property('SecureCustomProperties',';'.join(dict.fromkeys((secure+';'+approval).split(';'))))
    note='安装向导未完成或用户尚未同意协议，安装已停止。请重新运行完整安装向导。'
    db.execute('DELETE FROM `LaunchCondition` WHERE `Description`=?',(note,)).Close()
    # In the native UI, LaunchConditions runs before the license dialog.
    db.execute("DELETE FROM `ControlEvent` WHERE `Event`='[SW_NATIVE_APPROVED]'").Close()
    db.execute("UPDATE `ControlEvent` SET `Ordering`=2 WHERE `Dialog_`='LicenseAgreementDlg' "
               "AND `Control_`='Next' AND `Event`='NewDialog'").Close()
    db.insert('ControlEvent',('Dialog_','Control_','Event','Argument','Condition','Ordering'),
              ('LicenseAgreementDlg','Next','[SW_NATIVE_APPROVED]','1','LicenseAccepted = "1"',1))
    db.execute("DELETE FROM `CustomAction` WHERE `Action`='SW_RequireAgreement'").Close()
    db.insert('CustomAction',('Action','Type','Target'),('SW_RequireAgreement',19,note))
    db.execute("DELETE FROM `InstallExecuteSequence` WHERE `Action`='SW_RequireAgreement'").Close()
    db.insert('InstallExecuteSequence',('Action','Condition','Sequence'),
              ('SW_RequireAgreement','NOT (Installed OR REMOVE OR '
               '(SW_WIZARD_APPROVED=1 AND SW_ACCEPT_AGREEMENT=1) OR '
               '(NOT MSICLIENTUSESEMBEDDEDUI AND ((SW_NATIVE_APPROVED=1 AND LicenseAccepted=1) OR '
               '(UILevel<5 AND SW_ACCEPT_AGREEMENT=1))))',101))


def bootstrap(msi,exe,work,release):
    root=Path(__file__).resolve().parents[1]
    identity=work/'Setup.Identity.cs'
    identity.write_text('using System.Reflection;\n[assembly:AssemblyTitle("SpaceWeekly 安装")]\n'
        '[assembly:AssemblyFileVersion("'+release['version'].split('-')[0]+'")]\n'
        'internal static partial class Setup { const string PayloadHash="'+hashlib.sha256(msi.read_bytes()).hexdigest()+'"; }',encoding='utf-8')
    compiler=Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler),'/nologo','/codepage:65001','/target:winexe','/platform:x64','/optimize+',
        '/r:System.Windows.Forms.dll','/win32icon:'+str(root/'assets/sspo.ico'),'/win32manifest:'+str(root/'tools/windows/uninstall.manifest'),
        '/resource:'+str(msi)+',SpaceWeekly.msi','/out:'+str(exe),str(root/'tools/windows/Setup.cs'),str(identity)],check=True)
