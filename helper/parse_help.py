import json
import re

# extract help command from youtube-dl
# youtube-dl --help > help.txt
# run this script

EXPECTED_OFFSET = 4

json_output = []
with open("./help.txt", "r") as file:
    for line in file:
        no_start_space = re.sub(r'^\s+', '', line)
        diff = len(line) - len(no_start_space)
        if diff < EXPECTED_OFFSET:
            continue
        if diff > EXPECTED_OFFSET:
            json_output[-1]['description'] += " " + line.strip()
            continue
        
        line = line.strip()
        parts = line.split(' ')

        i = 0
        while i < len(parts) and len(parts[i]) == 0:
            i += 1
        
        if i == len(parts):
            continue 
                
        flag = parts[i]
        while flag[-1] == ',':
            i += 1
            flag = parts[i]
        i += 1

        argument = ""
        if i < len(parts) and len(parts[i]) > 0:
            argument = parts[i]
            i += 1

        while i < len(parts) and len(parts[i]) == 0:
            i += 1
        
        if i == len(parts):
            description = ''
        else:
            description = ' '.join(parts[i:])

        json_output.append({
            "flag": flag,
            "argument": argument,
            "description": description
        })

json.dump(json_output, open("./help.json", "w"), indent=4)