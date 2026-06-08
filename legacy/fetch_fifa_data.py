#!/usr/bin/env python
import BeautifulSoup
import urllib
import re
import csv

RE_CODE = re.compile('association=(...)/')
RE_TEAM = re.compile('title="(.+)"')
RE_SCORE = re.compile('(\d+):(\d+)')

URL = 'http://www.fifa.com/associations/library/_results.htmx?gender=m&idAssociation1=0&idAssociation2=0&MatchStatus=2&rangeDate=1&fromMonth=0&fromYear=0&toMonth=0&toYear=0&ClassificationGroup=0&r=nr&page=%d'


def parse_team(ref):
  return RE_CODE.search(ref).groups()[0], RE_TEAM.search(ref).groups()[0]

def parse_score(ref):
  res = RE_SCORE.search(ref)
  if res is None:
    print ref
  else:
    res = res.groups()
  return res

def parse_date(date):
  day, month, year = date.split('/')
  return '%s-%s-%s' % (year, month, day)

if __name__ == '__main__':
  writer = csv.DictWriter(file('results.csv', 'w'), ['date', 'location', 'code1', 'team1', 'code2', 'team2', 'goals1', 'goals2'])
  writer.writeheader()
  for idx in range(20, -2, -1):
    url = URL % -idx
    soup = BeautifulSoup.BeautifulSoup(urllib.urlopen(url).read())
    for row in soup.findAll('tr'):
      cols = [' '.join([str(x) for x in col.contents]) for col in row.findAll('td')]
      if cols[3].strip() in ('-', 'Abandoned'):
        continue
      score = parse_score(cols[3])
      if score is None:
        continue
      team1 = parse_team(cols[2])
      team2 = parse_team(cols[4])
      res = {'date': parse_date(cols[0].strip()),
             'location': cols[1].strip(),
             'code1': team1[0],
             'team1': team1[1],
             'goals1': score[0],
             'goals2': score[1],
             'code2': team2[0],
             'team2': team2[1]}
      writer.writerow(res)



