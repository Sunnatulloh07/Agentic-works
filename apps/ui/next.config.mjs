/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [{source:'/oauth/google/callback',headers:[
      {key:'Cache-Control',value:'no-store'},
      {key:'Referrer-Policy',value:'no-referrer'},
      {key:'X-Content-Type-Options',value:'nosniff'},
      {key:'X-Frame-Options',value:'DENY'}
    ]}];
  }
};
export default nextConfig;
