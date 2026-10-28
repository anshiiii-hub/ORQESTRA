FROM node:26-alpine AS build
WORKDIR /srv
ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:26-alpine AS development
WORKDIR /srv
COPY package*.json ./
RUN npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM nginx:alpine AS production
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /srv/dist /usr/share/nginx/html
EXPOSE 8080
