"""Workspace storage review and non-destructive periodic reminder."""
from datetime import datetime
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
        layout=QVBoxLayout(self);layout.addWidget(QLabel('工作区存储'))
        hint=QLabel('默认勾选超过保留期且无引用的临时文件。勾选正式素材或成果后，请先核对影响再确认删除。')
        hint.setWordWrap(True);layout.addWidget(hint)
        form=QFormLayout();self.enabled=QCheckBox('定期提醒清理');self.interval=QSpinBox();self.retention=QSpinBox()
        for spin in (self.interval,self.retention):spin.setRange(1,365);spin.setSuffix(' 天');spin.setMaximumWidth(120)
        warning=''
        try:data=cache.load()
        except (OSError,ValueError) as error:data=dict(cache.DEFAULTS);warning='维护记录读取失败，原文件保留：'+str(error)
        self.enabled.setChecked(data['enabled']);self.interval.setValue(data['interval_days']);self.retention.setValue(data['retention_days'])
        form.addRow(self.enabled);form.addRow('检查提醒周期',self.interval);form.addRow('未使用缓存保留',self.retention);layout.addLayout(form)
        for signal in (self.enabled.toggled,self.interval.valueChanged,self.retention.valueChanged):signal.connect(self.save_preferences)
        row=QHBoxLayout();self.scan_button=QPushButton('检查存储占用');self.scan_button.clicked.connect(lambda:self.workspace.guard(self.scan))
        self.clean_button=QPushButton('清理所选类别');self.clean_button.clicked.connect(lambda:self.workspace.guard(self.clean))
        self.cancel_button=QPushButton('取消检查或清理');self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.scan_button);row.addWidget(self.clean_button);row.addWidget(self.cancel_button);row.addStretch();layout.addLayout(row)
        self.table=QTableWidget(0,5);configure_table(self.table);self.table.setHorizontalHeaderLabels(['用途','文件数','占用','引用／锁定','最近时间'])
        self.table.horizontalHeaderItem(3).setToolTip('有引用文件数／运行中或受保护的文件数')
        self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,260);layout.addWidget(self.table,1)
        self.table.cellDoubleClicked.connect(self.category_details)
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

    def playback(self):
        return [player.source().toLocalFile()
                for player in (getattr(self.workspace,'preview_player',None),getattr(self.workspace.roundup,'player',None))
                if player is not None and player.source().isLocalFile()]

    def check_due(self):
        if not isValid(self.workspace):return
        if self.worker or self.workspace._pending_leave or not self.workspace.isVisible():return
        try:
            if cache.due():self.scan(automatic=True)
        except (OSError,ValueError) as error:self.status.setText('清理提醒检查失败：'+str(error))

    def set_running(self,running):
        self.scan_button.setEnabled(not running);self.clean_button.setEnabled(not running and bool(self.result and any(not r['blocked'] for r in self.result['inventory'])));self.cancel_button.setEnabled(running)

    def scan(self,*,automatic=False):
        if self.worker:return
        self.automatic=automatic;extra=self.references();active=self.active();playback=self.playback()
        self.worker=RoundupWorker(lambda _,token:cache.scan(token,extra=extra,active=active,in_use=playback),{},self,protect_cache=False)
        self.worker.finished.connect(self.finished);self.status.setText('正在检查工作区存储…');self.set_running(True);self.worker.start()

    def category_details(self,row,_):
        if not self.result:return
        item=self.table.item(row,0)
        files=self.category_rows[item.data(Qt.ItemDataRole.UserRole)]
        box=QMessageBox(QMessageBox.Icon.Information,item.text(),f'{len(files)} 个文件。展开查看路径、时间及引用。',parent=self)
        box.setDetailedText('\n'.join(f"{r['path']}  ·  {size_text(r['size'])}  ·  "
            f"{datetime.fromtimestamp(r['last_used']):%Y-%m-%d %H:%M}  ·  "
            f"{'有引用' if r['referenced'] else '无引用'}{'  ·  暂受保护' if r['blocked'] else ''}" for r in files))
        box.exec()

    def clean(self):
        if self.worker or not self.result:return
        groups={self.table.item(n,0).data(Qt.ItemDataRole.UserRole) for n in range(self.table.rowCount()) if self.table.item(n,0).checkState()==Qt.CheckState.Checked}
        rows=[r for key in groups for r in self.category_rows[key] if not r['blocked'] and (r['old'] or r['category'] in cache.FORMAL)]
        if not rows:return
        linked=sum(r['referenced'] for r in rows);formal=sum(r['category'] in cache.FORMAL for r in rows)
        message=f'删除 {len(rows)} 个文件，预计释放 {size_text(sum(r["size"] for r in rows))}？'
        if formal or linked:message+=f'\n其中 {formal} 个是素材或成果，{linked} 个仍有引用。删除后可能需要重新下载、生成或导出。'
        box=QMessageBox(QMessageBox.Icon.Question,'确认清理',message,parent=self)
        box.setDetailedText('\n'.join(r['path'] for r in rows))
        yes=box.addButton('删除所选文件',QMessageBox.ButtonRole.AcceptRole);box.addButton('取消',QMessageBox.ButtonRole.RejectRole);box.exec()
        if box.clickedButton()!=yes:return
        extra=self.references();playback=self.playback();self.automatic=False
        self.worker=RoundupWorker(lambda _,token:cache.clean(rows,token,extra=extra,active=self.active,in_use=playback,
            allow_formal=bool(formal),allow_referenced=bool(linked)),{'clean':True},self,protect_cache=False)
        self.worker.finished.connect(self.finished);self.status.setText('正在清理所选文件…');self.set_running(True);self.worker.start()

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
            self.category_rows={}
            for r in self.result['inventory']:
                suffix=' · 有引用' if r['referenced'] and r['category'] not in cache.FORMAL else ''
                self.category_rows.setdefault(cache.LABELS[r['category']]+suffix,[]).append(r)
            for label in sorted(self.category_rows):
                rows=self.category_rows[label]
                n=self.table.rowCount();self.table.insertRow(n);item=QTableWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,label)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if any(r['default'] for r in rows) else Qt.CheckState.Unchecked);self.table.setItem(n,0,item)
                for col,text in ((1,str(len(rows))),(2,size_text(sum(r['size'] for r in rows))),
                                 (3,f"{sum(r['referenced'] for r in rows)} ／ {sum(r['blocked'] for r in rows)}"),
                                 (4,datetime.fromtimestamp(max(r['last_used'] for r in rows)).strftime('%Y-%m-%d %H:%M'))):
                    cell=QTableWidgetItem(text);cell.setFlags(Qt.ItemFlag.ItemIsEnabled);self.table.setItem(n,col,cell)
                item.setToolTip('\n'.join(r['path'] for r in rows))
            self.status.setText(f'默认勾选 {self.result["count"]} 个文件，预计释放 {size_text(self.result["bytes"])}。双击类别查看文件。'+('部分清单无法读取，已暂停删除。' if self.result['reference_warning'] else ''))
            if self.automatic and (self.result['count'] or any(r['old'] and r['category'] in cache.FORMAL for r in self.result['inventory'])):self.show_reminder()
        worker.deleteLater();self.set_running(False)

    def show_reminder(self):
        files=self.result['inventory']
        video=sum(r['size'] for r in files if r['category'] in ('downloads','generated'))
        exports=sum(r['size'] for r in files if r['category']=='exports')
        cache.reminded();box=QMessageBox(QMessageBox.Icon.Information,'缓存清理提醒',
            f'旧缓存可清理 {self.result["count"]} 个，约 {size_text(self.result["bytes"])}。\n'
            f'已下载视频及生成素材占用 {size_text(video)}，导出成果占用 {size_text(exports)}。',parent=self.workspace)
        view=box.addButton('查看并清理',QMessageBox.ButtonRole.AcceptRole);later=box.addButton('稍后提醒',QMessageBox.ButtonRole.RejectRole)
        off=box.addButton('关闭定期提醒',QMessageBox.ButtonRole.DestructiveRole)
        def chosen(_):
            if box.clickedButton()==view:
                self.workspace.show_settings();self.workspace.settings_tabs.setCurrentWidget(self)
            elif box.clickedButton()==off:self.enabled.setChecked(False)
            else:cache.snooze()
            self.reminder=None;box.deleteLater()
        box.finished.connect(chosen);self.reminder=box;box.open()
