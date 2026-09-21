"""Strict single-decision parser shared by both planner modes."""
import json


def unique_object(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate model JSON key')
        out[key]=value
    return out


def reject_constant(value):raise ValueError('Non-finite model JSON value')


def parse_decision(response, maximum_bytes=20000):
    if not isinstance(response,dict):raise ValueError('Model response must be an object')
    choices=response.get('choices')
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):
        raise ValueError('Exactly one model choice required')
    choice=choices[0]
    if choice.get('finish_reason')!='stop' or not isinstance(choice.get('message'),dict):
        raise ValueError('Complete model response required')
    message=choice['message']
    if message.get('refusal') or message.get('tool_calls'):
        raise ValueError('Refusal or alternate tool-call envelope is not a JSON decision')
    text=message.get('content')
    if not isinstance(text,str) or not 1<=len(text.encode('utf-8'))<=maximum_bytes:
        raise ValueError('Bounded model decision text required')
    try:
        value=json.loads(text,object_pairs_hook=unique_object,parse_constant=reject_constant)
    except (RecursionError,json.JSONDecodeError):
        raise ValueError('Malformed model decision JSON') from None
    if not isinstance(value,dict):raise ValueError('Model decision must be an object')
    # Do not depend on a Python/JSON implementation-specific recursion limit.
    stack=[(value,0)];visited=0
    while stack:
        node,depth=stack.pop();visited+=1
        if depth>32 or visited>5000:raise ValueError('Model JSON structure exceeds limits')
        if isinstance(node,dict):stack.extend((child,depth+1) for child in node.values())
        elif isinstance(node,list):stack.extend((child,depth+1) for child in node)
    return value
