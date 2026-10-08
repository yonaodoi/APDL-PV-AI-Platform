from pathlib import Path
from datetime import datetime
import argparse, re, shutil

parser=argparse.ArgumentParser()
parser.add_argument('--project',required=True)
args=parser.parse_args()
root=Path(args.project).resolve()
css=root/'app/static/css/platform.css'
dashboard=root/'app/templates/dashboard.html'
base=root/'app/templates/base.html'
for p in (css,dashboard,base):
    if not p.is_file():raise SystemExit('Project file not found: '+str(p))
new_dashboard=dashboard.read_text(encoding='utf-8-sig')
# Remove the complete conditional reminder panel, including its nested plural check.
start_pattern=re.compile(r'{%\s*if\s+open_follow_up_reminder_count\s*%}')
while True:
    opening=start_pattern.search(new_dashboard)
    if not opening:break
    depth=1;end=None
    for token in re.finditer(r'{%\s*(if\b|endif\b).*?%}',new_dashboard[opening.end():],re.S):
        depth+=1 if token.group(1)=='if' else -1
        if depth==0:end=opening.end()+token.end();break
    if end is None:raise SystemExit('Dashboard has an unclosed reminder condition. No files changed.')
    new_dashboard=new_dashboard[:opening.start()]+new_dashboard[end:]
# Also remove standalone shortcuts added by other revisions. Sidebar lives in base.html.
def remove_shortcut(match):
    tag=match.group()
    return '' if re.search(r'follow_up_tasks|follow_up_reminders|Follow[- ]up\s+tasks|Overdue\s+reminders',tag,re.I) else tag
new_dashboard=re.sub(r'<a\b[^>]*>.*?</a>',remove_shortcut,new_dashboard,flags=re.S|re.I)
new_dashboard=re.sub(r'<button\b[^>]*>.*?</button>',remove_shortcut,new_dashboard,flags=re.S|re.I)
marker='/* APDL WHITE GREEN BUTTON LABELS v1 */'
new_css=css.read_text(encoding='utf-8-sig')
if marker in new_css:new_css=new_css[:new_css.index(marker)].rstrip()+'\n'
new_css+='\n'+Path(__file__).with_name('button-labels.css').read_text(encoding='utf-8')
new_base=base.read_text(encoding='utf-8-sig')
# Update only this stylesheet version so the browser fetches the new text rules.
new_base=re.sub(r"(filename=['\"]css/platform\.css['\"],\s*v=)['\"][^'\"]*['\"]",r"\1'button-labels-20261005'",new_base)
plans={css:new_css,dashboard:new_dashboard,base:new_base}
backup=root/'design_backups'/('button-labels-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
for path in plans:
    saved=backup/path.relative_to(root);saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,saved)
for path,content in plans.items():path.write_text(content,encoding='utf-8')
print('Green button labels updated. Home-page follow-up shortcuts removed.')
print('Safety Cases menu links retained. Backup:',backup)
print('Restart Flask and refresh with Ctrl+F5.')
