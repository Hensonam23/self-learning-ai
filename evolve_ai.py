import random
import numpy as np
import http.server  # Fixed import
import socketserver
import pygame
from pygame.locals import *
import os
import time
import threading

# Machine Spirit parameters
TARGET_PHRASE = "for the omnissiah"
GENOME_LENGTH = 15
POPULATION_SIZE = 100  # Increased for better convergence
MUTATION_RATE = 0.05

# Global variables for living AI state
best_genome = ""
input_text = ""  # For user interactions
talking = False  # Tracks if AI is "talking"
evolution_thread = None

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

# Live display with background and waveform
def display_interface():
    global input_text, talking
    try:
        os.environ['SDL_VIDEODRIVER'] = 'x11'  # Use X11 for DSI compatibility
        os.environ['DISPLAY'] = ':0'  # Use default display
        pygame.init()
        screen = pygame.display.set_mode((800, 480))  # Resizable window
        pygame.display.set_caption("Machine Spirit Interface")
        clock = pygame.time.Clock()

        # Load background image from current directory
        background = pygame.image.load("/home/aaron/self-learning-ai/background.png")
        background = pygame.transform.scale(background, (800, 480))  # Scale to screen

        font = pygame.font.SysFont('Arial', 30)  # Smaller font
        running = True
        waveform_data = np.zeros(400)  # Initial waveform data
        waveform_pos = 0  # Position for scrolling waveform
        last_talk_time = 0

        while running:
            current_time = time.time()
            for event in pygame.event.get():
                if event.type == QUIT:
                    running = False
                elif event.type == KEYDOWN:
                    if event.key == K_RETURN:
                        if "hello" in input_text.lower() or "sad" in input_text.lower():
                            talking = True
                            last_talk_time = current_time
                        else:
                            talking = False
                        input_text = ""  # Clear input
                    elif event.key == K_BACKSPACE:
                        input_text = input_text[:-1]
                    else:
                        input_text += event.unicode  # Allow typing

            # Update background
            screen.blit(background, (0, 0))

            # Update waveform if talking (simulated for 2 seconds after input)
            if talking and (current_time - last_talk_time) < 2:
                waveform_data = np.roll(waveform_data, -1)  # Shift left
                waveform_data[-1] = np.sin(current_time * 5) * 50 + np.random.normal(0, 10, 1)[0]  # New amplitude
            else:
                waveform_data = np.zeros(400)  # Reset if not talking

            # Draw waveform (bottom 100 pixels)
            for x in range(400):
                y = int(240 + waveform_data[(waveform_pos + x) % 400])  # Center at 240, height 100
                if x > 0:
                    pygame.draw.line(screen, (0, 255, 0), (x-1 + 200, 240 + int(waveform_data[(waveform_pos + x - 1) % 400])),
                                    (x + 200, y), 2)  # Green waveform

            # Render evolved text
            text = font.render(best_genome, True, (200, 200, 200))  # Gray text
            text_rect = text.get_rect(center=(400, 100))
            screen.blit(text, text_rect)

            # Render input text
            input_render = font.render(input_text, True, (200, 200, 200))
            input_rect = input_render.get_rect(center=(400, 300))
            screen.blit(input_render, input_rect)

            pygame.display.flip()
            clock.tick(30)  # 30 FPS
            waveform_pos = (waveform_pos + 1) % 400  # Move waveform

    except Exception as e:
        print(f"Touchscreen error: {e}")
    finally:
        pygame.quit()

# Start background evolution thread
evolution_thread = threading.Thread(target=evolve_background)
evolution_thread.daemon = True  # Runs in background, exits with main
evolution_thread.start()

# Start resizable display
display_interface()

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
