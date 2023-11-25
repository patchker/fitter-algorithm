from datetime import date, timedelta
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import random

app = FastAPI()

# Database connection
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
    meal_type: str  # New field for meal type


class DietPlan(BaseModel):
    date: date
    meals: List[Meal]
    total_calories: int

class DietRequest(BaseModel):
    start_date: date
    end_date: date
    meals_per_day: int
    preferred_ingredients: List[str]
    max_calories: int

# Combined API endpoint
# Combined API endpoint
@app.post("/generate-diet/")
async def generate_diet(request: DietRequest):
    conn = get_db_connection()
    meals = conn.execute('SELECT id, name, ingredients, calories, meal_type FROM meals').fetchall()
    conn.close()

    # Filter suitable meals
    suitable_meals = []
    for meal in meals:
        ingredients_list = meal['ingredients'].split(',')
        meal_type = meal['meal_type'] if meal['meal_type'] is not None else 'default_type'
        if any(ingredient in ingredients_list for ingredient in request.preferred_ingredients) and meal[
            'calories'] <= request.max_calories:
            suitable_meals.append(Meal(
                id=meal['id'],
                name=meal['name'],
                ingredients=ingredients_list,
                calories=meal['calories'],
                meal_type=meal_type))

    if not suitable_meals:
        raise HTTPException(status_code=404, detail="No suitable meals found")

    # Generate diet plan
    diet_plan = []
    current_date = request.start_date
    while current_date <= request.end_date:
        daily_plan = generate_daily_plan(suitable_meals, request.meals_per_day, request.max_calories)
        diet_plan.append(DietPlan(date=current_date, meals=daily_plan['meals'], total_calories=daily_plan['total_calories']))
        current_date += timedelta(days=1)

    return diet_plan
def generate_daily_plan(meals, meals_per_day, max_calories):
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

    # Distribute meals across the selected types based on calorie distribution
    for meal_type, calorie_ratio in selected_meal_types.items():
        meals_in_type = meals_by_type.get(meal_type, [])
        allocated_calories = max_calories * calorie_ratio

        for _ in range(len(meals_in_type)):
            if not meals_in_type or total_calories >= max_calories:
                break

            meal = select_meal_close_to_calorie_target(meals_in_type, allocated_calories)
            if meal:
                selected_meals.append(meal)
                total_calories += meal.calories
                meals_in_type.remove(meal)

    return {'meals': selected_meals, 'total_calories': total_calories}


def group_meals_by_type(meals):
    meals_by_type = {}
    for meal in meals:
        if meal.meal_type not in meals_by_type:
            meals_by_type[meal.meal_type] = []
        meals_by_type[meal.meal_type].append(meal)
    return meals_by_type

def select_meal_close_to_calorie_target(meals, calorie_target):
    if not meals:
        return None

    # Sort the meals by how close their calories are to the target
    sorted_meals = sorted(meals, key=lambda meal: abs(meal.calories - calorie_target))

    # Choose from the top N closest meals to add randomness
    top_n = 3  # You can adjust this number as needed
    top_choices = sorted_meals[:min(len(sorted_meals), top_n)]

    # Randomly select one of the top choices
    return random.choice(top_choices) if top_choices else None
