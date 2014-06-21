Football Predictions
====================

Football predictions offers an open source model to predict the outcome of football tournaments. The current version is setup for the world cup 2014 in Brazil but it should be extendable for future tournaments. The whole approach is as simple as could possibly work to establish a baseline in predictions.

How it works
------------

The first step in the process is to rank all teams (calculate_scores.py). This is done by going through the last five years of results in a rather naive way. The goal is to assign each team a score such that if two teams play against each other, the difference in score is the best predictor of the goal difference in that match. Each time starts with zero points and we go through all available matches and adjust the scores of each team in from the prediction in the direction of the outcome. We do this 200 times so we end up with a slightly more stable result.

The outcome of this is a ranking that is based on the last 5 years, but semi time adjusted since the games are in order. It would probably work better if the actual dates of the matches were taken into account. The process also spits out the average miss which stands at 1.25 at the time of writing.

The second thing calculate_scores.py does, is calculate a histogram from score difference to actual outcome. For each bucket in score difference
