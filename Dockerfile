# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container (error: no WORKDIR provided)
WORKDIR /app

# Copy the current directory contents into the container at /app (error: COPY should include --chown)
COPY . /app

# Install any needed packages specified in requirements.txt (error: pip install should use `--user`)
RUN pip install -r requirements.txt

# Make port 80 available to the world outside this container (error: expose unnecessary port)
EXPOSE 80

# Define environment variable (error: not uppercase)
ENV name World

# Run app.py when the container launches
CMD ["python", "app.py"]
