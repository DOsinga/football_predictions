Football Predictions
====================

Football predictions offers an open source model to predict the outcome of football tournaments. The current version is setup for the world cup 2014 in Brazil but it should be extendable for future tournaments. The whole approach is as simple as could possibly work to establish a baseline in predictions.

Building the model
------------------

The first step in the process is to rank all teams (calculate_scores.py). This is done by going through the last five years of results in a rather naive way. The goal is to assign each team a score such that if two teams play against each other, the difference in score is the best predictor of the goal difference in that match. Each time starts with zero points and we go through all available matches and adjust the scores of each team in from the prediction in the direction of the outcome. We do this 200 times so we end up with a slightly more stable result.

The outcome of this is a ranking that is based on the last 5 years, but semi time adjusted since the games are in order. It would probably work better if the actual dates of the matches were taken into account. The process also spits out the average miss which stands at 1.25 at the time of writing.

The second thing calculate_scores.py does, is calculate a histogram from score difference to actual outcome. Going through the historic data one more time, for each prediction, we look at what the actual outcome was and bucket these results by the log of the predicted score difference. For example for score difference from [0.1 .. 0.2] we get [152, 162, 217], which means that for the teams that were between 0.1 and 0.2 better than another, 217 won that match, 162 drew and 152 lost.


Running the prediction
----------------------

Now that we have a model, we can run the prediction. simulator.py takes care of this. The tournament is encoded in a separate file aptly called brazil_1014.tournament. It contains the various rounds and who's playing aginst who. When you run the simulator, it goes through all matches in the tournament. The ones that already have been played and are present in results.csv are taken for fact, the result are simulated.

Simulating a game works by looking up the score difference between two teams and looking up the outcome bucket for that difference and then make a weighted choice for the outcome, win, draw or loss.
We then use a weighted choice to pick a win, draw or loss for this matches based on that histogram.

Again, this is not very sophisticated. The outcome is encoded as a goal diference, but always 2-0, 1-1 or 0-2. The knock-out phase is modelled as groups of 2, as is the final.

By running the simulation 100 000 times, we get a nice distributon of outcomes:

 * Brazil 35.55%
 * Argentina 13.29%
 * France 13.22%
 * Germany 11.13%
 * Netherlands 8.46%
 * Chile 6.76%

At the time of writing the outcome is remarkable similar to the must more complicated model of fivethirtyeight at http://fivethirtyeight.com/interactives/world-cup/ 




