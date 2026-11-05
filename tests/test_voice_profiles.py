"""Profile identity and invalidation without model inference."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services import voice_profiles
from services.roundup_audio import audio_key


class VoiceProfileTests(unittest.TestCase):
    def test_rename_keeps_audio_and_reference_update_changes_the_key(self):
        with tempfile.TemporaryDirectory(dir='build',prefix='我的 音色 ') as folder:
            root=Path(folder).resolve()
            original=root/'录音.wav';original.write_bytes(b'first')
            reference=root/'参考.wav';reference.write_bytes(b'normalized first')
            with patch.object(voice_profiles,'directory',return_value=root/'profiles'):
                identifier=voice_profiles.save('本人','第一份实际原文',str(root/'base'),original,reference)
                draft=dict(tts=dict(speaker=identifier,model='',python='',style=''),pronunciations={})
                segment=dict(spoken='本周发射综述。')
                old_key=audio_key(draft,segment)
                voice_profiles.rename(identifier,'本人新版')
                self.assertEqual(audio_key(draft,segment),old_key)
                self.assertEqual(voice_profiles.list_profiles()[0]['name'],'本人新版')
                reference.write_bytes(b'normalized second')
                self.assertEqual(voice_profiles.save('本人新版','第二份实际原文',str(root/'base'),original,reference,identifier),identifier)
                self.assertNotEqual(audio_key(draft,segment),old_key)
                self.assertTrue(original.is_file())
                voice_profiles.delete(identifier)
                self.assertFalse(voice_profiles.list_profiles())
                with self.assertRaises(ValueError):audio_key(draft,segment)
