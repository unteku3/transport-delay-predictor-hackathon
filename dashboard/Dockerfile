FROM nginx:stable-alpine
COPY dashboard/nginx.conf /etc/nginx/conf.d/default.conf
COPY dashboard/index.html dashboard/support.js /usr/share/nginx/html/
COPY dashboard/vendor /usr/share/nginx/html/vendor
