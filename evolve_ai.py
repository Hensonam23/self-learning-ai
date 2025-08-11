import random
import numpy as np
import http.server
import socketserver
import pygame
from pygame.locals import *
import os

# Machine Spirit parameters
TARGET_PHRASE = "for the emperor"
GENOME_LENGTH = 15
POPULATION_SIZE = 20
GENERATIONS = 50
MUTATION_RATE = 0.2

# Create random genome
def create_genome():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz ', k=GENOME_LENGTH))

# Fitness
def fitness(genome):
    score = sum(a == b for a, b in zip(genome, TARGET_PHRASE))
    return score / GENOME_LENGTH

# Mutate
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

# Display live, reactive "face" on DSI touchscreen
def display_face(best_genome):
    try:
        os.environ['SDL_VIDEODRIVER'] = 'x11'  # Use X11 for DSI compatibility
        os.environ['DISPLAY'] = ':0'  # Use default display
        pygame.init()
        screen = pygame.display.set_mode((800, 480))
        pygame.display.set_caption("Machine Spirit Face")
        clock = pygame.time.Clock()
        
        font = pygame.font.SysFont('Arial', 30)  # Smaller font
        input_text = ""  # For user communication
        mouth_curve = 0  # 0 neutral, positive for smile, negative for frown
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == QUIT:
                    running = False
                elif event.type == KEYDOWN:
                    if event.key == K_RETURN:
                        # React to input
                        if "hello" in input_text.lower():
                            mouth_curve = 3.14  # Smile
                        elif "sad" in input_text.lower():
                            mouth_curve = -3.14  # Frown
                        else:
                            mouth_curve = 0  # Neutral
                        input_text = ""  # Clear input
                    elif event.key == K_BACKSPACE:
                        input_text = input_text[:-1]
                    else:
                        input_text += event.unicode
            
            screen.fill((0, 0, 0))  # Black background
            # Draw face
            pygame.draw.circle(screen, (255, 255, 255), (200, 200), 50)  # Left eye
            pygame.draw.circle(screen, (255, 255, 255), (600, 200), 50)  # Right eye
            pygame.draw.arc(screen, (255, 255, 255), (300, 300, 200, 100), 3.14 - mouth_curve, 6.28, 5)  # Mouth with curve
            # Render evolved text
            text = font.render(best_genome, True, (255, 255, 255))
            text_rect = text.get_rect(center=(400, 400))
            screen.blit(text, text_rect)
            # Render input text
            input_render = font.render(input_text, True, (255, 255, 255))
            input_rect = input_render.get_rect(center=(400, 450))
            screen.blit(input_render, input_rect)
            pygame.display.flip()
            clock.tick(30)  # 30 FPS
        
        pygame.quit()
    except Exception as e:
        print(f"Touchscreen error: {e}")

# Evolution loop
population = [create_genome() for _ in range(POPULATION_SIZE)]
best_genome = ""
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
display_face(best_genome)

# HTTP server
PORT = 8089
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Machine Spirit serving at port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Machine Spirit has been silenced")
        httpd.server_close()
