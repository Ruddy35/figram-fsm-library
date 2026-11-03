import json,sys
def strip(x):
    if isinstance(x,dict): return {k:strip(v) for k,v in x.items() if k!='detached'}
    if isinstance(x,list): return [strip(v) for v in x]
    return x
a=[strip(json.loads(l)) for l in open(sys.argv[1])]
b=[json.loads(l) for l in open(sys.argv[2])]
print("equal up to detached:", a==b, len(a))
sys.exit(0 if a==b else 1)
