"""Release updates reuse the production exit/cancel lifecycle."""
from PySide6.QtCore import Signal,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QCheckBox,QPlainTextEdit,QProgressBar,QFileDialog,QMessageBox
from ui.launch_roundup_dialog import RoundupWorker
from config import app_version
from services import updates
from shiboken6 import isValid


class UpdatePanel(QWidget):
    progress_changed=Signal(int)
    def __init__(self,workspace):
        super().__init__();self.workspace=workspace;self.worker=None;self.action='';self.background=False;self.waiting=False;self.after_verify=False;self.exit_action='update'
        layout=QVBoxLayout(self);layout.addWidget(QLabel('软件更新'))
        self.current=QLabel('当前版本：'+app_version());layout.addWidget(self.current)
        self.version=QLabel('更新来源：GitHub Releases · Windows x64 稳定版');layout.addWidget(self.version)
        self.auto=QCheckBox('启动后在后台检查更新');layout.addWidget(self.auto)
        try:state=updates.load()
        except (OSError,ValueError):state={}
        self.auto.setChecked(state.get('automatic',True));self.auto.toggled.connect(lambda value:workspace.guard(lambda:updates.save(automatic=value)))
        row=QHBoxLayout();self.check=QPushButton('检查更新');self.download=QPushButton('下载更新');self.restart=QPushButton('重启更新');self.later=QPushButton('稍后更新');self.cancel=QPushButton('取消下载 / 检查')
        for b in (self.check,self.download,self.restart,self.later,self.cancel):row.addWidget(b)
        row.addStretch();layout.addLayout(row)
        self.check.clicked.connect(lambda:self.start('check'));self.download.clicked.connect(lambda:self.start('download'))
        self.restart.clicked.connect(self.request_restart);self.later.clicked.connect(self.defer);self.cancel.clicked.connect(self.cancel_work)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.hide();layout.addWidget(self.progress)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.notes=QPlainTextEdit();self.notes.setReadOnly(True);self.notes.setPlaceholderText('更新说明');layout.addWidget(self.notes,1)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.hide()
        toggle=QCheckBox('查看错误详情');toggle.toggled.connect(self.details.setVisible);layout.addWidget(toggle);layout.addWidget(self.details)
        if updates.profile().get('qa'):
            local=QPushButton('本地验收：选择更新源目录');local.clicked.connect(self.choose_local);layout.addWidget(local)
        legacy=QPushButton('从旧便携版或工作区导入数据');legacy.clicked.connect(lambda:workspace.guard(workspace.import_legacy));layout.addWidget(legacy)
        self.uninstall=QPushButton('卸载 SpaceWeekly');self.uninstall.clicked.connect(self.request_uninstall);layout.addWidget(self.uninstall)
        self.progress_changed.connect(self.progress.setValue);self.refresh()
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.wait_idle);self.timer.start()
        QTimer.singleShot(3000,self.initial_check)

    def refresh(self):
        try:state=updates.load()
        except (OSError,ValueError) as error:
            self.status.setText('更新设置需要检查；制作功能可继续使用。')
            self.details.setPlainText(str(error));state={}
        available=state.get('available');pending=state.get('pending');idle=self.worker is None;installed=updates.installed()
        self.check.setEnabled(idle and installed);self.download.setEnabled(idle and installed and bool(available))
        self.restart.setEnabled(idle and bool(pending));self.later.setEnabled(bool(pending) or self.waiting);self.cancel.setEnabled(not idle)
        self.uninstall.setEnabled(idle and installed)
        target=pending or (available or {}).get('TargetFullRelease')
        if target:
            self.version.setText('可用版本：'+target['Version']+f' · 完整包 {target["Size"]/1024/1024:.1f} MB')
            self.notes.setPlainText(target.get('NotesMarkdown',''))
        else:
            self.version.setText('更新来源：GitHub Releases · Windows x64 稳定版');self.notes.clear()
        if not installed:self.status.setText('源码或开发构建：在线更新未启用，制作功能可继续使用。')
        elif pending:self.status.setText('更新已下载，等待你选择重启；稍后更新不会重新下载。')

    def initial_check(self):
        if not isValid(self.workspace):return
        if self.workspace._pending_leave or not self.workspace.isVisible() or not updates.installed():return
        self.start('status',background=True)

    def choose_local(self):
        path=QFileDialog.getExistingDirectory(self,'选择本地 Velopack 更新源')
        if path:self.workspace.guard(lambda:updates.save(local_source=path));self.version.setText('本地验收源：'+path)

    def start(self,action,*,background=False):
        if self.worker:return
        self.background=background;self.action=action;self.progress.setValue(0);self.progress.setVisible(action in ('download','verify'))
        self.status.setText({'check':'正在检查公开更新…','status':'正在恢复待更新状态…','download':'正在下载并校验更新包…','verify':'正在复查待更新包…'}[action])
        self.worker=RoundupWorker(lambda _,token:updates.run_job(action,token,self.progress_changed.emit),{},self,protect_cache=False)
        self.worker.finished.connect(self.finished);self.worker.start();self.refresh()

    def finished(self):
        worker=self.worker;self.worker=None;action=self.action
        if worker.error:
            self.details.setPlainText(worker.error);self.status.setText('更新操作未完成，可重试；当前版本和作品仍可使用。');self.after_verify=False
        elif worker.cancelled:self.status.setText('已取消，已有更新包保留，可重试。');self.after_verify=False
        else:
            value=worker.value
            if action=='check':
                updates.save(available=value['available'])
                self.status.setText('发现可用更新，可查看说明后下载。' if value['available'] else '当前没有可用更新。')
            elif action=='status' and not value.get('pending'):
                updates.save(pending=None,available=None);self.status.setText('没有等待安装的更新，可手动检查新版本。')
            else:updates.save(pending=value.get('pending'))
        worker.deleteLater();self.refresh()
        if action=='status' and not worker.error and not worker.cancelled and self.auto.isChecked() and not updates.load().get('pending'):
            self.start('check',background=True)
        elif action=='verify' and self.after_verify and not worker.error and not worker.cancelled:
            self.after_verify=False;self.coordinate_exit()

    def cancel_work(self):
        if self.worker:self.worker.token.cancel()

    def defer(self):
        self.waiting=False;self.after_verify=False;self.status.setText('稍后更新；当前制作与已下载包保留。');self.refresh()

    def request_restart(self):
        self.exit_action='update'
        self.after_verify=True;self.start('verify')

    def request_uninstall(self):
        if QMessageBox.question(self,'卸载 SpaceWeekly','卸载只移除程序，工作区、词典、图片、作品和语音模型会保留。\n继续后会先保存并结束制作任务，再打开系统卸载程序。')!=QMessageBox.StandardButton.Yes:return
        self.exit_action='uninstall';self.coordinate_exit()

    def coordinate_exit(self):
        p=self.workspace
        action='卸载' if self.exit_action=='uninstall' else '更新'
        if p.busy():
            box=QMessageBox(QMessageBox.Icon.Question,'安全'+action,'当前有制作任务。请选择等待完成，或安全取消后'+action+'。',parent=self)
            wait=box.addButton('等待完成',QMessageBox.ButtonRole.AcceptRole);cancel=box.addButton('取消任务后'+action,QMessageBox.ButtonRole.DestructiveRole)
            box.addButton('稍后'+action,QMessageBox.ButtonRole.RejectRole);box.exec()
            if box.clickedButton()==wait:self.waiting=True;self.status.setText('等待任务完成；仍可稍后'+action+'。');return
            if box.clickedButton()!=cancel:return
            p.guard(lambda:p.request_leave(self.exit_action,cancel_running=True));return
        p.guard(lambda:p.request_leave(self.exit_action))

    def wait_idle(self):
        if not isValid(self.workspace):self.timer.stop();return
        if self.waiting and not self.workspace.busy():
            self.waiting=False
            if self.exit_action=='uninstall':self.coordinate_exit()
            else:self.request_restart()
