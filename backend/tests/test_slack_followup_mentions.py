"""Pure classifier behavior: execute the actual definitions without HTTP I/O."""
import ast
import re
import unittest
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'sportsassets/slack_bridge.py'
tree=ast.parse(path.read_text())
ns={'re':re}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef)
                             and n.name=='classify'],type_ignores=[]),str(path),'exec'),ns)
classify=ns['classify']
CFG={'team':'T_TEST','app':'A_TEST','channels':{'C_TEST'},'managers':{'U_MANAGER'}}

def event(text='What changed?', bot='U_DEREK'):
 return {'team_id':'T_TEST','api_app_id':'A_TEST','event_id':'Ev_TEST',
         'authorizations':[{'team_id':'T_TEST','user_id':bot,'is_bot':True}],
         'event':{'type':'message','channel_type':'group','channel':'C_TEST',
                  'user':'U_MANAGER','text':text,'thread_ts':'1000.1','ts':'1001.1'}}

# Slack user IDs are alphanumeric; use realistic shapes in mention cases.
BOT='U0DEREK123'
SAM='U0SAM12345'
TOOL='U0C66F8QAF3'

class FollowupMentions(unittest.TestCase):
 def test_plain_thread_reply_unchanged(self):
  p=event();p.pop('authorizations')
  out,why=classify(p,CFG)
  self.assertIsNone(why);self.assertTrue(out['followup'])

 def test_teammate_mention_keeps_thread_and_text(self):
  p=event('Please explain this to <@'+SAM+'>.',BOT)
  out,why=classify(p,CFG)
  self.assertIsNone(why);self.assertEqual(out['text'],p['event']['text'])
  self.assertEqual(out['thread'],'1000.1')

 def test_connected_tool_attribution_does_not_drop_followup(self):
  p=event('Which policy?\n*Sent using* <@'+TOOL+'>',BOT)
  out,why=classify(p,CFG)
  self.assertIsNone(why);self.assertTrue(out['followup'])

 def test_self_mention_is_left_for_app_mention_once(self):
  for tag in ('<@'+BOT+'>','<@'+BOT+'|Derek>'):
   p=event(tag+' please explain',BOT)
   self.assertEqual(classify(p,CFG),(None,'MENTION_HANDLED_AS_APP_MENTION'))
   p['event']['type']='app_mention'
   out,why=classify(p,CFG)
   self.assertIsNone(why);self.assertFalse(out['followup'])

 def test_substring_user_id_is_not_self_mention(self):
  out,why=classify(event('<@'+BOT+'9> can review',BOT),CFG)
  self.assertIsNone(why);self.assertTrue(out['followup'])

 def test_unproved_or_ambiguous_authorization_refuses(self):
  for auth in (None,{},[],[None],[{'team_id':'WRONG','user_id':BOT,'is_bot':True}],
               [{'team_id':'T_TEST','user_id':BOT,'is_bot':False}],
               [{'team_id':'T_TEST','user_id':BOT,'is_bot':True},
                {'team_id':'T_TEST','user_id':'U_OTHER','is_bot':True}]):
   p=event('<@'+SAM+'> please review',BOT);p['authorizations']=auth
   self.assertEqual(classify(p,CFG),(None,'MENTION_BOT_IDENTITY_UNPROVED'))

 def test_sender_channel_team_and_app_checks_remain(self):
  cases=[('team_id','OTHER','WRONG_TEAM'),('api_app_id','OTHER','WRONG_APP'),
         ('user','U_OTHER','USER_NOT_A_MANAGER'),('channel','C_OTHER','CHANNEL_NOT_ALLOWED'),
         ('bot_id','B_OTHER','BOT_OR_SUBTYPE'),('subtype','bot_message','BOT_OR_SUBTYPE')]
  for field,value,why in cases:
   p=event('<@'+SAM+'> can review',BOT)
   (p if field in ('team_id','api_app_id') else p['event'])[field]=value
   self.assertEqual(classify(p,CFG),(None,why))

 def test_no_top_level_message_admitted(self):
  p=event('<@'+SAM+'> please review',BOT);p['event'].pop('thread_ts')
  self.assertEqual(classify(p,CFG),(None,'NOT_A_THREAD_REPLY'))

 def test_large_or_nonstring_message_still_refused(self):
  for text in ('x'*4001,77):
   self.assertEqual(classify(event(text,BOT),CFG),(None,'TEXT_SIZE'))

if __name__=='__main__':unittest.main()
