import re

patterns = [
    re.compile(r'(?:reconcile|merge|combine)\s+(?:the\s+)?(?:annotated\s+)?bams?', re.I),
    re.compile(r'download.*(?:and|then)\s+(?:run|analyze|process)', re.I),
    re.compile(r'get.*from\s+encode.*(?:and|then)\s+(?:run|analyze|dogme)', re.I),
    re.compile(r'compare\s+(?:these|the|two|my|both|all)?\s*(?:samples?|results?|workflows?)', re.I),
    re.compile(r'(?:treated|control)\s+(?:vs?\.?|versus)\s+', re.I),
    re.compile(r'(?:download|get|fetch)\s+.*encode.*(?:compare|vs)', re.I),
    re.compile(r'compare\s+(?:my\s+)?(?:local|sample).*(?:to|with|against)\s+.*(?:encode|public)', re.I),
    re.compile(r'(?:run|do|perform)\s+(?:a\s+)?(?:differential\s+expression|DE\s+analysis)', re.I),
]
test_queries = [
    "compare alzheimer vs control directRNA from encode",
    "alzheimer control encode",
    "download directRNA data from ENCODE and compare to local",
    "compare my local samples to ENCODE directRNA data",
    "treated vs control DE analysis",
    "reconcile the annotated BAMs from my workflows",
    "get K562 from encode and then run dogme",
    "run differential expression on my results",
    "compare my sample against encode public data",
    "fetch DRNA from encode and compare vs local",
]
for q in test_queries:
    matched = any(p.search(q) for p in patterns)
    tag = "MATCH" if matched else "MISS "
    print(f"  {tag}: {q}")
