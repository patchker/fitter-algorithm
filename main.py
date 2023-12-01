import asyncio
from fastapi import FastAPI, HTTPException, BackgroundTasks
import requests

from pydantic import BaseModel, HttpUrl
from datetime import date, timedelta
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import random
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()
task_queue = asyncio.Queue()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000"],  # Lista źródeł, które mają być dozwolone
    allow_credentials=True,
    allow_methods=["*"],  # Dopuszczenie wszystkich metod, np. GET, POST, PUT, DELETE
    allow_headers=["*"],  # Dopuszczenie wszystkich nagłówków
)

def get_db_connection():
    conn = sqlite3.connect('meals.db')
    conn.row_factory = sqlite3.Row
    return conn

# Data models
class Meal(BaseModel):
    id: int
    name: str
    ingredients: List[str]
    calories: int
    protein: float = None
    fat: float = None
    carbs: float = None
    meal_type: str
    allergens: List[str] = []
    base_grams: int = 0
    portions: int = 1  # Nowe pole


class DietRequest(BaseModel):
    start_date: date
    end_date: date
    meals_per_day: int
    max_calories: int
    dietary_preferences: List[str] = []
    allergens_to_avoid: List[str] = []
    user_weight: int
    not_preferred_ingredients: List[str] = []
    callback_url: HttpUrl  # Dodane pole dla URL callback

class DietPlan(BaseModel):
    date: date
    meals: List[Meal]
    total_calories: int

    def to_dict(self):
        return {
            "date": self.date.isoformat(),
            "meals": [meal.dict() for meal in self.meals],
            "total_calories": self.total_calories
        }



async def process_task_queue():
    while True:
        diet_request, callback_url = await task_queue.get()
        try:
            # Przetwarzanie żądania diety (tutaj umieść swoją logikę)
            diet_plan = await generate_diet_logic(diet_request)
            # Informowanie serwera Django o zakończeniu zadania
            requests.post(callback_url, json={'status': 'completed', 'diet_plan': diet_plan})
        except Exception as e:
            # W przypadku błędu, informuj serwer Django
            requests.post(callback_url, json={'status': 'error', 'error': str(e)})
        finally:
            task_queue.task_done()

asyncio.create_task(process_task_queue())

@app.post("/generate-diet/")
async def generate_diet(request: DietRequest, background_tasks: BackgroundTasks):
    callback_url = request.callback_url
    background_tasks.add_task(task_queue.put, (request, callback_url))
    return {"message": "Zadanie zostało dodane do kolejki"}

async def generate_diet_logic(diet_request):
    conn = get_db_connection()
    meals = conn.execute(
        'SELECT id, name, ingredients, calories, protein, fat, carbs, meal_type, allergens, base_grams FROM meals').fetchall()
    conn.close()

    ingredient_usage = {}  # Słownik do śledzenia częstotliwości użycia składników

    # Filter suitable meals
    suitable_meals = []
    for meal_row in meals:
        meal = dict(meal_row)
        ingredients_list = [ingredient.strip() for ingredient in meal['ingredients'].split(',')]
        meal_type = meal['meal_type'] if meal['meal_type'] is not None else 'default_type'
        allergens = meal.get('allergens', '')
        allergens_set = set(allergens.split(',')) if allergens is not None else set()

        if not allergens_set.intersection(diet_request.allergens_to_avoid) and \
                not any(non_pref in ingredients_list for non_pref in diet_request.not_preferred_ingredients):
            suitable_meals.append(Meal(
                id=meal['id'],
                name=meal['name'],
                ingredients=ingredients_list,
                calories=meal['calories'],
                protein=meal['protein'],  # Dodane pole
                fat=meal['fat'],  # Dodane pole
                carbs=meal['carbs'],  # Dodane pole
                meal_type=meal_type,
                base_grams=meal['base_grams'],
            )),

    if not suitable_meals:
        raise HTTPException(status_code=404, detail="No suitable meals found")

    user_macros = calculate_macros(diet_request.user_weight, diet_request.max_calories - 500)

    # Generate diet plan
    diet_plan = []
    current_date = diet_request.start_date
    while current_date <= diet_request.end_date:
        daily_plan = generate_daily_plan(suitable_meals, diet_request.meals_per_day, diet_request.max_calories - 400, user_macros,
                                         ingredient_usage)
        diet_plan.append(
            DietPlan(date=current_date, meals=daily_plan['meals'], total_calories=daily_plan['total_calories']))
        current_date += timedelta(days=1)

    return [diet_plan.to_dict() for diet_plan in diet_plan]




def generate_daily_plan(meals, meals_per_day, max_calories, user_macros, ingredient_usage):
    # Define the meal types and calorie distribution based on the number of meals per day
    meal_types_calorie_distribution = {
        1: {'dinner': 1.0},
        2: {'breakfast': 0.3, 'dinner': 0.7},
        3: {'breakfast': 0.25, 'lunch': 0.35, 'afternoon_snack': 0.4},
        4: {'breakfast': 0.2, 'lunch': 0.25, 'dinner': 0.35, 'afternoon_snack': 0.2},
        5: {'breakfast': 0.2, 'lunch': 0.2, 'dinner': 0.3, 'afternoon_snack': 0.15, 'evening_snack': 0.15}
    }

    selected_meal_types = meal_types_calorie_distribution.get(meals_per_day, {})
    meals_by_type = group_meals_by_type(meals)

    selected_meals = []
    total_calories = 0

    selected_meal_types = meal_types_calorie_distribution.get(meals_per_day, {})
    meals_by_type = group_meals_by_type(meals)

    # Inicjalizacja zmiennych do śledzenia pozostałych makroskładników i kalorii
    remaining_calories = max_calories
    remaining_protein = user_macros['protein']
    remaining_fat = user_macros['fat']
    remaining_carbs = user_macros['carbs']

    selected_meals = []
    total_calories = 0
    total_protein = 0
    total_fat = 0
    total_carbs = 0
    selected_meals_ids = set()

    for meal_type, calorie_ratio in selected_meal_types.items():
        meals_in_type = meals_by_type.get(meal_type, [])
        type_calories = max_calories * calorie_ratio  # Kalorie przeznaczone na ten typ posiłku

        while meals_in_type and type_calories > 0:
            meal, portions = select_meal_close_to_macros_target(meals_in_type, type_calories, remaining_protein,
                                                                remaining_fat, remaining_carbs, max_calories,
                                                                selected_meals_ids, ingredient_usage)
            if meal:
                # Aktualizacja pozostałych makroskładników i kalorii
                type_calories -= meal.calories * portions
                remaining_calories -= meal.calories * portions
                remaining_protein -= meal.protein * portions
                remaining_fat -= meal.fat * portions
                remaining_carbs -= meal.carbs * portions

                # Przygotowanie i dodanie posiłku do wybranej listy
                new_meal = meal.copy()  # Tworzy kopię posiłku
                new_meal.calories *= portions
                new_meal.protein *= portions
                new_meal.fat *= portions
                new_meal.carbs *= portions
                new_meal.portions = portions  # Ustawienie liczby porcji

                selected_meals.append(new_meal)
                total_calories += new_meal.calories
                total_protein += new_meal.protein
                total_fat += new_meal.fat
                total_carbs += new_meal.carbs
                selected_meals_ids.add(meal.id)
                meals_in_type.remove(meal)
                for ingredient in new_meal.ingredients:
                    ingredient_usage[ingredient] = ingredient_usage.get(ingredient, 0) + 1

    return {'meals': selected_meals, 'total_calories': total_calories, 'total_protein': total_protein,
            'total_fat': total_fat, 'total_carbs': total_carbs}


def group_meals_by_type(meals):
    meals_by_type = {}
    for meal in meals:
        if meal.meal_type not in meals_by_type:
            meals_by_type[meal.meal_type] = []
        meals_by_type[meal.meal_type].append(meal)
    return meals_by_type


def select_meal_close_to_macros_target(meals, remaining_calories, remaining_protein, remaining_fat, remaining_carbs,
                                       max_daily_calories, selected_meals_ids, ingredient_usage):
    best_fit = None
    best_fit_portions = 0
    best_fit_diff = float('inf')

    for meal in meals:
        if meal.id in selected_meals_ids:
            continue
        for portions in range(1, 6):  # Ograniczenie do 5 porcji
            if meal.calories * portions > max_daily_calories:
                break  # Przerwanie pętli, jeśli przekracza limit kalorii

            calorie_diff = abs(remaining_calories - meal.calories * portions)
            protein_diff = abs(remaining_protein - meal.protein * portions)
            fat_diff = abs(remaining_fat - meal.fat * portions)
            carbs_diff = abs(remaining_carbs - meal.carbs * portions)

            # Agregacja różnic do pojedynczej wartości
            total_diff = calorie_diff + protein_diff + fat_diff + carbs_diff

            # Modyfikacja oceny posiłku w zależności od częstotliwości składników
            ingredient_frequency = sum(ingredient_usage.get(ing, 0) for ing in meal.ingredients)
            total_diff += ingredient_frequency ** 2

            if total_diff < best_fit_diff:
                best_fit = meal
                best_fit_portions = portions
                best_fit_diff = total_diff

    return best_fit, best_fit_portions


def calculate_macros(weight, total_calories):
    # Przykładowe procentowe rozkłady makroskładników
    protein_percentage = 0.20  # 20% kalorii z białka
    fat_percentage = 0.30  # 30% kalorii z tłuszczu
    carbs_percentage = 0.50  # 50% kalorii z węglowodanów

    protein_calories = total_calories * protein_percentage
    fat_calories = total_calories * fat_percentage
    carbs_calories = total_calories * carbs_percentage

    protein_grams = protein_calories / 4  # 1 gram białka = 4 kcal
    fat_grams = fat_calories / 9  # 1 gram tłuszczu = 9 kcal
    carbs_grams = carbs_calories / 4  # 1 gram węglowodanów = 4 kcal

    return {
        "protein": protein_grams,
        "fat": fat_grams,
        "carbs": carbs_grams
    }
