import random
import numpy as np
import http.server
import socketserver
import pygame
from pygame.locals import *
import os

# Machine Spirit parameters
TARGET_PHRASE = "for the emperor"
GENOME_LENGTH = 15  # Matches length of "for the emperor"
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

# Display evolving "face" on touchscreen
def display_face(best_genome):
    os.putenv('SDL_VIDEODRIVER', 'fbcon')  # Use framebuffer for touchscreen
    os.putenv('SDL_FBDEV', '/dev/fb0')    # Default framebuffer device
    pygame.init()
    screen = pygame.display.set_mode((800, 480))  # Adjust to your touchscreen resolution (e.g., 800x480 for 7" display)
    pygame.display.set_caption("Machine Spirit Face")
    clock = pygame.time.Clock()
    
    font = pygame.font.SysFont('Arial', 40)
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == QUIT:
                running = False
        
        screen.fill((0, 0, 0))  # Black background
        text = font.render(best_genome, True, (255, 255, 255))  # White text for current best phrase
        screen.blit(text, (100, 200))  # Center it
        pygame.display.flip()
        clock.tick(30)  # 30 FPS
    
    pygame.quit()

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

# Display on touchscreen
display_face(best_genome)

# Simple HTTP server for Machine Spirit (port 8089, host 0.0.0.0)
PORT = 8089
Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Machine Spirit serving at port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Machine Spirit has been silenced")
        httpd.server_close()
