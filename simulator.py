#!/usr/bin/env python
# coding=utf-8

import random
import math

from collections import defaultdict, Counter

ITERATIONS = 10000

GROUP_A = [
  ('Brazil', 'Croatia', 3, 1),
  ('Mexico', 'Cameroon', 1, 0),
  ('Brazil', 'Mexico', 0, 0),
  ('Cameroon', 'Croatia', 0, 4),
  ('Croatia', 'Mexico',),
  ('Cameroon', 'Brazil',),
]

GROUP_B = [
  ('Spain', 'Netherlands', 1, 5),
  ('Chile', 'Australia', 3, 1),
  ('Australia', 'Netherlands', 2, 3),
  ('Spain', 'Chile', 0, 2),
  ('Australia', 'Spain',),
  ('Netherlands', 'Chile',),
]

GROUP_C = [
  ('Colombia', 'Greece', 3, 0),
  ("Côte d'Ivoire", 'Japan', 2, 1),
  ("Colombia", "Côte d'Ivoire", 2, 1),
  ('Japan', 'Greece', 0, 0),
  ('Japan', 'Colombia',),
  ('Greece', "Côte d'Ivoire",)
]

GROUP_D = [
  ('Uruguay', 'Costa Rica', 1, 3),
  ('England', 'Italy', 1, 2),
  ('Uruguay', 'England', 2, 1),
  ('Italy', 'Costa Rica', 0, 1),
  ('Italy', 'Uruguay',),
  ('Costa Rica', 'England',)
]

GROUP_E = [
  ('Switzerland', 'Ecuador', 2, 1),
  ('France', 'Honduras', 3, 0),
  ('Switzerland', 'France', 2, 5),
  ('Honduras', 'Ecuador', 1, 2),
  ('Honduras', 'Switzerland',),
  ('Ecuador', 'France',),
]

GROUP_F = [
  ('Argentina', 'Bosnia and Herzegovina', 2, 1),
  ('Iran', 'Nigeria', 0, 0),
  ('Argentina', 'Nigeria',),
  ('Nigeria', 'Bosnia and Herzegovina',),
  ('Nigeria', 'Argentina'),
  ('Bosnia and Herzegovina', 'Iran',),
]

GROUP_G = [
  ('Germany', 'Portugal', 4, 0),
  ('Ghana', 'USA', 2, 1),
  ('Germany', 'Ghana',),
  ('USA', 'Portugal',),
  ('USA', 'Germany',),
  ('Portugal', 'Ghana',),
]

GROUP_H = [
  ('Belgium', 'Algeria', 2, 1),
  ('Russia', 'Korea Republic', 1, 1),
  ('Belgium', 'Russia',),
  ('Korea Republic', 'Algeria',),
  ('Korea Republic', 'Belgium',),
  ('Algeria', 'Russia',),
]

model = None
win_loss_histogram = None

def simulate_game(team1, team2):
  score1 = model[team1]
  score2 = model[team2]
  difference = score1 - score2
  bucket = abs(difference)
  if bucket < 0.1:
    bucket = 0
  else:
    bucket = math.log(bucket) / math.log(2) - math.log(0.1) / math.log(2)
  histogram_1 = win_loss_histogram[int(bucket)]
  histogram_2 = win_loss_histogram[int(bucket) + 1]
  fraction = bucket - int(bucket)
  histogram = [int(histogram_1[i] * (1 - fraction) + histogram_2[i] * fraction) for i in range(3)]
  total = sum(histogram)
  r = random.randint(0, total - 1)
  res = 0
  if r <= histogram[0]:
    res = -1
  elif r > histogram[1] + histogram[0]:
    res = 1
  if difference < 0:
    res = -res
  return 1 + res, 1 - res


def simulate_knock_out(team1, team2):
  g1, g2 = simulate_game(team1, team2)
  if g1 > g2:
    return team1
  elif g2 > g1:
    return team2
  if random.randint(0, 1) == 0:
    return team1
  else:
    return team2

def simulate_group(group):
  assert len(group) == 6
  points = defaultdict(int)
  goals_pro = defaultdict(int)
  goals_against = defaultdict(int)
  for game in group:
    team1 = game[0]
    team2 = game[1]
    if len(game) == 4:
      goals1 = game[2]
      goals2 = game[3]
    else:
      goals1, goals2 = simulate_game(team1, team2)
    if goals1 == goals2:
      points[team1] += 1
      points[team2] += 1
    elif goals1 > goals2:
      points[team1] += 3
    else:
      points[team2] += 3
    goals_pro[team1] += goals1
    goals_against[team1] += goals2
    goals_pro[team2] += goals2
    goals_against[team2] += goals1
  teams = sorted(goals_against.keys(),
                 key=lambda team: points[team] + float(goals_pro[team] - goals_against[team]) / 100 + float(goals_pro[team] / 10000), reverse=True)
  return teams[0], teams[1]



def group_chances(group):
  results = defaultdict(lambda: [0, 0])
  for i in range(100000):
    team1, team2 = simulate_group(group)
    results[team1][0] += 1
    results[team2][1] += 1

  for k, v in results.items():
    print '%s: %2.2f%% %2.2f%%' % (k, v[0] / 1000., v[1] / 1000.)


def simulate_cup():
  A1, A2 = simulate_group(GROUP_A)
  B1, B2 = simulate_group(GROUP_B)
  C1, C2 = simulate_group(GROUP_C)
  D1, D2 = simulate_group(GROUP_D)
  E1, E2 = simulate_group(GROUP_E)
  F1, F2 = simulate_group(GROUP_F)
  G1, G2 = simulate_group(GROUP_G)
  H1, H2 = simulate_group(GROUP_H)

  R1 = simulate_knock_out(A1, B2)
  R2 = simulate_knock_out(C1, D2)
  R3 = simulate_knock_out(E1, F2)
  R4 = simulate_knock_out(G1, H2)
  R5 = simulate_knock_out(B1, A2)
  R6 = simulate_knock_out(D1, C2)
  R7 = simulate_knock_out(F1, E2)
  R8 = simulate_knock_out(H1, G2)

  Q1 = simulate_knock_out(R1, R2)
  Q2 = simulate_knock_out(R3, R4)
  Q3 = simulate_knock_out(R5, R6)
  Q4 = simulate_knock_out(R7, R8)

  F1 = simulate_knock_out(Q1, Q2)
  F2 = simulate_knock_out(Q3, Q4)

  W = simulate_knock_out(F1, F2)

  return W

if __name__ == '__main__':
  model = file('model.txt').read().splitlines()
  win_loss_histogram = eval(model[0])
  del model[0]
  model = [l.split(':') for l in model]

  model = {team: float(score) for team, score in model}

  res = Counter()
  for i in range(ITERATIONS):
    res[simulate_cup()] += 1

  for team, c in res.most_common():
    print team, '%2.2f%%' % ((100.0 * c) / ITERATIONS)
