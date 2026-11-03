"""Workspace cache review and non-destructive periodic reminder in existing settings."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QCheckBox,QSpinBox,QPushButton,QTableWidget,QTableWidgetItem,QMessageBox
from ui.component import configure_table
from ui.launch_roundup_dialog import RoundupWorker
from services import cache_maintenance as cache
from shiboken6 import isValid


def size_text(value):
    return f'{value/1024/1024:.1f} MB' if value>=1024*1024 else f'{value/1024:.1f} KB'


class CacheMaintenancePanel(QWidget):
    def __init__(self,workspace):
        super().__init__();self.workspace=workspace;self.worker=None;self.result=None;self.automatic=False;self.reminder=None
        layout=QVBoxLayout(self);layout.addWidget(QLabel('新闻图片与缓存'))
        hint=QLabel('正式配图保存在当前工作区的 news-images。清理仅处理已识别的可再生成缓存，保护稿件、素材、词典、语音模型和制作成果。')
        hint.setWordWrap(True);layout.addWidget(hint)
        form=QFormLayout();self.enabled=QCheckBox('定期提醒清理');self.interval=QSpinBox();self.retention=QSpinBox()
        for spin in (self.interval,self.retention):spin.setRange(1,365);spin.setSuffix(' 天')
        warning=''
        try:data=cache.load()
        except (OSError,ValueError) as error:data=dict(cache.DEFAULTS);warning='维护记录读取失败，原文件保留：'+str(error)
        self.enabled.setChecked(data['enabled']);self.interval.setValue(data['interval_days']);self.retention.setValue(data['retention_days'])
        form.addRow(self.enabled);form.addRow('检查提醒周期',self.interval);form.addRow('未使用缓存保留',self.retention);layout.addLayout(form)
        for signal in (self.enabled.toggled,self.interval.valueChanged,self.retention.valueChanged):signal.connect(self.save_preferences)
        row=QHBoxLayout();self.scan_button=QPushButton('检查可清理内容');self.scan_button.clicked.connect(lambda:self.workspace.guard(self.scan))
        self.clean_button=QPushButton('清理所选类别');self.clean_button.clicked.connect(lambda:self.workspace.guard(self.clean))
        self.cancel_button=QPushButton('取消检查或清理');self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.scan_button);row.addWidget(self.clean_button);row.addWidget(self.cancel_button);row.addStretch();layout.addLayout(row)
        self.table=QTableWidget(0,3);configure_table(self.table);self.table.setHorizontalHeaderLabels(['缓存类别','文件数','预计释放'])
        self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,260);layout.addWidget(self.table,1)
        self.status=QLabel('尚未检查；提醒不会自动删除文件。');self.status.setWordWrap(True);layout.addWidget(self.status)
        last=data.get('last_result')
        if isinstance(last,dict):
            self.status.setText(f'上次清理删除 {last.get("deleted",0)} 个文件，释放 {size_text(last.get("bytes",0))}；未删除 {len(last.get("failed",[]))} 项。')
            self.status.setToolTip('\n'.join(r['path']+'：'+r['reason'] for r in last.get('failed',[])))
        if warning:self.status.setText(warning)
        self.timer=QTimer(self);self.timer.setInterval(60000);self.timer.timeout.connect(self.check_due);self.timer.start()
        QTimer.singleShot(2000,self.check_due);self.set_running(False)

    def save_preferences(self):
        self.workspace.guard(lambda:cache.update(enabled=self.enabled.isChecked(),interval_days=self.interval.value(),retention_days=self.retention.value()))

    def active(self):
        p=self.workspace
        return any((p.worker,p.aux_worker,getattr(p,'image_worker',None),getattr(getattr(p,'updates',None),'worker',None),p.roundup.worker,p.cards.worker,p.intro.worker,p.intro.preview_worker,p.downloads.worker,p.voice_setup.worker))

    def references(self):
        p=self.workspace;extra=[p.content_state()]
        if hasattr(p,'preview_player'):extra.append(p.preview_player.source().toLocalFile())
        return extra

    def check_due(self):
        if not isValid(self.workspace):return
        if self.worker or self.workspace._pending_leave or not self.workspace.isVisible():return
        try:
            if cache.due():self.scan(automatic=True)
        except (OSError,ValueError) as error:self.status.setText('清理提醒检查失败：'+str(error))

    def set_running(self,running):
        self.scan_button.setEnabled(not running);self.clean_button.setEnabled(not running and bool(self.result and self.result['files']));self.cancel_button.setEnabled(running)

    def scan(self,*,automatic=False):
        if self.worker:return
        self.automatic=automatic;extra=self.references();active=self.active()
        self.worker=RoundupWorker(lambda _,token:cache.scan(token,extra=extra,active=active),{},self,protect_cache=False)
        self.worker.finished.connect(self.finished);self.status.setText('正在检查可再生成缓存…');self.set_running(True);self.worker.start()

    def clean(self):
        if self.worker or not self.result:return
        kinds={self.table.item(n,0).data(Qt.ItemDataRole.UserRole) for n in range(self.table.rowCount()) if self.table.item(n,0).checkState()==Qt.CheckState.Checked}
        rows=[r for r in self.result['files'] if r['category'] in kinds]
        if not rows:return
        box=QMessageBox(QMessageBox.Icon.Question,'确认清理',f'清理 {len(rows)} 个缓存文件，预计释放 {size_text(sum(r["size"] for r in rows))}？\n删除前会重新检查引用、文件变化和运行状态。',parent=self)
        yes=box.addButton('清理所选缓存',QMessageBox.ButtonRole.AcceptRole);box.addButton('取消',QMessageBox.ButtonRole.RejectRole);box.exec()
        if box.clickedButton()!=yes:return
        extra=self.references();self.automatic=False
        self.worker=RoundupWorker(lambda _,token:cache.clean(rows,token,extra=extra,active=self.active),{'clean':True},self,protect_cache=False)
        self.worker.finished.connect(self.finished);self.status.setText('正在清理所选缓存…');self.set_running(True);self.worker.start()

    def cancel(self):
        if self.worker:self.worker.token.cancel()

    def finished(self):
        worker=self.worker;self.worker=None
        if worker.error:self.status.setText('缓存处理失败：'+worker.error)
        elif worker.cancelled:self.status.setText('已取消；未删除的文件保留。')
        elif worker.payload.get('clean'):
            result=worker.value;self.result=None;self.table.setRowCount(0)
            text=f'已删除 {result["deleted"]} 个文件，实际释放 {size_text(result["bytes"])}。'
            if result['cancelled']:text+='已取消，剩余文件保留。'
            if result['failed']:text+='未删除 '+str(len(result['failed']))+' 项。'
            self.status.setText(text);self.status.setToolTip('\n'.join(r['path']+'：'+r['reason'] for r in result['failed']))
        else:
            self.result=worker.value;self.table.setRowCount(0)
            for kind,label in cache.LABELS.items():
                rows=[r for r in self.result['files'] if r['category']==kind]
                if not rows:continue
                n=self.table.rowCount();self.table.insertRow(n);item=QTableWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,kind)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked);self.table.setItem(n,0,item)
                for col,text in ((1,str(len(rows))),(2,size_text(sum(r['size'] for r in rows)))):
                    cell=QTableWidgetItem(text);cell.setFlags(Qt.ItemFlag.ItemIsEnabled);self.table.setItem(n,col,cell)
                item.setToolTip('\n'.join(r['path'] for r in rows))
            self.status.setText(f'可清理 {self.result["count"]} 个文件，预计释放 {size_text(self.result["bytes"])}。'+('部分引用记录无法读取，已保守保护相关缓存。' if self.result['reference_warning'] else ''))
            if self.automatic and self.result['count']:self.show_reminder()
        worker.deleteLater();self.set_running(False)

    def show_reminder(self):
        cache.reminded();box=QMessageBox(QMessageBox.Icon.Information,'缓存清理提醒',
            f'发现 {self.result["count"]} 个超过保留期的缓存文件，可释放约 {size_text(self.result["bytes"])}。正式素材会保留。',parent=self.workspace)
        view=box.addButton('查看并清理',QMessageBox.ButtonRole.AcceptRole);later=box.addButton('稍后提醒',QMessageBox.ButtonRole.RejectRole)
        off=box.addButton('关闭定期提醒',QMessageBox.ButtonRole.DestructiveRole)
        def chosen(_):
            if box.clickedButton()==view:
                self.workspace.show_settings();self.workspace.settings_tabs.setCurrentWidget(self)
            elif box.clickedButton()==off:self.enabled.setChecked(False)
            else:cache.snooze()
            self.reminder=None;box.deleteLater()
        box.finished.connect(chosen);self.reminder=box;box.open()
