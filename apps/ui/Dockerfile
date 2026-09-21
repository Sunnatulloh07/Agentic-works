FROM node:22-alpine AS build
WORKDIR /app
COPY apps/ui/package*.json ./
RUN npm ci
COPY apps/ui/ ./
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app /app
USER node
EXPOSE 3000
CMD ["npm","start","--","-H","0.0.0.0"]
