FROM node:22.12-alpine

WORKDIR /app

# Install pnpm
RUN npm install -g pnpm

# Copy project files
COPY . .

# Install dependencies
RUN pnpm install

# Expose port
EXPOSE 5173

# Run dev server
CMD ["pnpm", "dev"]
