import random
import string

def generate_random_code(length=10):
    # Define the characters to use in the code
    characters = string.ascii_letters + string.digits  # Includes both letters and digits
    # Generate a random code
    random_code = ''.join(random.choice(characters) for _ in range(length))
    return random_code

# Example usage
if __name__ == "__main__":
    code_length = int(input("Enter the length of the code: "))
    random_code = generate_random_code(code_length)
    print(f"Generated Random Code: {random_code}")
