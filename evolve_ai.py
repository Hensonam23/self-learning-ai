import random
import numpy as np
import http.server
import socketserver
import pygame
from pygame.locals import *
import os
import time
import threading

# Machine Spirit parameters
TARGET_PHRASE = "for the omnissiah"
GENOME_LENGTH = 15
POPULATION_SIZE = 20
MUTATION_RATE = 0.2

# Global variables for living AI state
best_genome = ""
input_text = ""  # For user interactions
mouth_curve = 0  # Mouth state
blink_time = 0  # For blinking eyes
evolution_thread = None
fullscreen = False  # Toggle for future AI control

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

# Background evolution thread for constant self-learning
def evolve_background():
    global best_genome
    population = [create_genome() for _ in range(POPULATION_SIZE)]
    while True:
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
        time.sleep(5)  # Update every 5 seconds

# Live, resizable "face" display (Adeptus Mechanicus themed: mechanical skull, binary eyes, cog mouth)
def display_face():
    global input_text, mouth_curve, blink_time, fullscreen
    try:
        os.environ['SDL_VIDEODRIVER'] = 'x11'  # Use X11 for DSI compatibility
        os.environ['DISPLAY'] = ':0'  # Use default display
        pygame.init()
        screen = pygame.display.set_mode((800, 480))  # Resizable window
        pygame.display.set_caption("Machine Spirit Face")
        clock = pygame.time.Clock()
        
        font = pygame.font.SysFont('Arial', 30)  # Smaller font
        running = True
        while running:
            current_time = time.time()
            for event in pygame.event.get():
                if event.type == QUIT:
                    running = False
                elif event.type == KEYDOWN:
                    if event.key == K_f:  # Toggle fullscreen for future AI control
                        fullscreen = not fullscreen
                        if fullscreen:
                            screen = pygame.display.set_mode((800, 480), FULLSCREEN)
                        else:
                            screen = pygame.display.set_mode((800, 480))
                    elif event.key == K_RETURN:
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
            # Draw Adeptus Mechanicus servo-skull face
            # Mechanical skull outline
            pygame.draw.polygon(screen, (150, 150, 150), [(250, 100), (550, 100), (600, 200), (600, 300), (550, 400), (250, 400), (200, 300), (200, 200)], 5)  # Angular skull
            # Binary red eyes with blink (glow effect)
            eye_color = (255, 0, 0) if current_time - blink_time > 2 else (100, 0, 0)  # Dim for blink
            if current_time - blink_time > 2:
                blink_time = current_time
            pygame.draw.circle(screen, eye_color, (300, 200), 40)  # Left eye with binary pulse
            pygame.draw.circle(screen, eye_color, (500, 200), 40)  # Right eye with binary pulse
            # Cog mouth with pistons (mechanical wave)
            mouth_wave = 3.14 + (0.5 * random.random() - 0.25)  # Slight random wave
            for tooth in range(8):  # More cog teeth for Mechanicus
                tooth_x = 300 + tooth * 25
                pygame.draw.rect(screen, (200, 200, 200), (tooth_x, 300, 15, 20))  # Cog teeth
            pygame.draw.arc(screen, (200, 200, 200), (300, 300, 200, 100), 3.14 - mouth_curve - mouth_wave, 6.28, 5)  # Mouth arc with pistons
            # Subtle Omnissiah symbol (simplified gear and skull)
            pygame.draw.circle(screen, (100, 100, 100), (400, 150), 20, 2)  # Small gear
            pygame.draw.line(screen, (100, 100, 100), (390, 140), (410, 140), 2)  # Top of skull
            # Render evolved text below the face
            text = font.render(best_genome, True, (200, 200, 200))  # Gray for Mechanicus aesthetic
            text_rect = text.get_rect(center=(400, 400))
            screen.blit(text, text_rect)
            # Render input text
            input_render = font.render(input_text, True, (200, 200, 200))
            input_rect = input_render.get_rect(center=(400, 450))
            screen.blit(input_render, input_rect)
            pygame.display.flip()
            clock.tick(30)  # 30 FPS
        
        pygame.quit()
    except Exception as e:
        print(f"Touchscreen error: {e}")

# Start background evolution thread
evolution_thread = threading.Thread(target=evolve_background)
evolution_thread.daemon = True  # Runs in background, exits with main
evolution_thread.start()

# Start resizable display
display_face()

# HTTP server (runs after display quits)
PORT = 8089
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Machine Spirit serving at port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Machine Spirit has been silenced")
        httpd.server_close()
