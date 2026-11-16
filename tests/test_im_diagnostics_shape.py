import json
import pytest
from app.im_diagnostics import im_response_shape
from app.browser.douyin_im_pb import _enc_ld,_enc_v


def test_json_summary_never_includes_values_or_unknown_keys():
    raw=json.dumps({'status_code':123,'body':'SECRET','SECRETKEY':'SECRET','cmd':0}).encode()
    result=im_response_shape(raw)
    assert result['format']=='json' and result['numeric']=={'cmd':0,'status_code':123}
    assert result['other_keys']==1 and 'SECRET' not in json.dumps(result)


def test_wire_structure_complete_without_values():
    raw=_enc_v(1,100)+_enc_ld(4,b'SECRET')
    result=im_response_shape(raw)
    assert result['complete'] and result['format']=='protobuf_wire'
    assert result['fields']==[{'field':1,'wire':0,'encoded_length':1},{'field':4,'wire':2,'encoded_length':7}]
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('raw',[b'\x22\x05ab',b'\x09ab',b'\x08\x80',b'\x00',b'\x0f',b'\x08'+b'\xff'*11])
def test_malformed_or_truncated_is_never_complete(raw):
    result=im_response_shape(raw)
    assert result['format']=='unknown_or_truncated' and not result['complete']


def test_format_limits_and_digest():
    assert im_response_shape(b'')['format']=='empty'
    assert im_response_shape(b'\x1f\x8bSECRET')['format']=='gzip'
    assert im_response_shape(b'<html>SECRET')['format']=='markup'
    assert im_response_shape(b'x'*4097)['format']=='over_limit'
    assert len(im_response_shape(b'x')['sha256'])==64
