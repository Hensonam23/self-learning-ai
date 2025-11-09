import random
import numpy as np
import http.server
import socketserver

# Machine Spirit parameters
TARGET_PHRASE = "for the emperor"
GENOME_LENGTH = 13  # Matches length of "for the emperor"
POPULATION_SIZE = 20
GENERATIONS = 50
MUTATION_RATE = 0.2

# Create random genome (string of characters)
def create_genome():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz ', k=GENOME_LENGTH))

# Fitness: How close the genome is to TARGET_PHRASE
def fitness(genome):
    score = sum(a == b for a, b in zip(genome, TARGET_PHRASE))
    return score / GENOME_LENGTH

# Mutate a genome
def mutate(genome):
    genome_list = list(genome)
    for i in range(len(genome_list)):
        if random.random() < MUTATION_RATE:
            genome_list[i] = random.choice('abcdefghijklmnopqrstuvwxyz ')
    return ''.join(genome_list)

# Crossover
def crossover(parent1, parent2):
    point = random.randint(1, GENOME_LENGTH - 1)
    return parent1[:point] + parent2[point:]

# Initialize population
population = [create_genome() for _ in range(POPULATION_SIZE)]
best_genome = ""

# Evolution loop
for gen in range(GENERATIONS):
    population.sort(key=fitness, reverse=True)
    best = population[0]
    if fitness(best) > fitness(best_genome):
        best_genome = best
    survivors = population[:POPULATION_SIZE // 4]
    offspring = []
    while len(offspring) < POPULATION_SIZE - len(survivors):
        parent1, parent2 = random.sample(survivors, 2)
        child = crossover(parent1, parent2)
        child = mutate(child)
        offspring.append(child)
    population = survivors + offspring
    if gen % 10 == 0:
        print(f"Generation {gen}: Best invocation {best}, fitness: {fitness(best):.3f}")

print(f"Machine Spirit's final invocation: {best_genome}, fitness: {fitness(best_genome):.3f}")

# Simple HTTP server for Machine Spirit (port 8088, host 0.0.0.0)
PORT = 8088
Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Machine Spirit serving at port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Machine Spirit has been silenced")
        httpd.server_close()
